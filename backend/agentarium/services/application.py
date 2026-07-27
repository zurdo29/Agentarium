from __future__ import annotations

from pathlib import Path
from typing import Any

from agentarium.agents import RoleCatalog, RoleRunner
from agentarium.approvals import ApprovalPolicy
from agentarium.artifacts import ArtifactStore
from agentarium.config.settings import Settings, get_settings
from agentarium.domain.enums import ApprovalStatus, ProjectStatus, WorkItemStatus
from agentarium.domain.models import (
    ApprovalRequest,
    ExecutionEvent,
    Project,
    WorkItem,
    new_id,
)
from agentarium.execution.scheduler import ResourceScheduler
from agentarium.llm import ProviderRegistry
from agentarium.memory import ContextBuilder
from agentarium.orchestration import Orchestrator
from agentarium.repositories import Database, Repository


class ApplicationService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        repository: Repository,
        orchestrator: Orchestrator,
        scheduler: ResourceScheduler,
        roles: RoleCatalog,
    ) -> None:
        self.settings = settings
        self.database = database
        self.repository = repository
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.roles = roles
        self.approval_policy = ApprovalPolicy()

    def initialize(self) -> int:
        self.settings.ensure_directories()
        self.database.create_all()
        recovered = self.repository.recover_interrupted()
        return recovered

    def create_project(self, goal: str, title: str | None = None) -> Project:
        cleaned = goal.strip()
        if not cleaned:
            raise ValueError("Goal cannot be empty")
        generated_title = title.strip() if title and title.strip() else self._title(cleaned)
        project = self.repository.create_project(Project(title=generated_title, goal=cleaned))
        correlation_id = new_id()
        self.repository.add_event(
            ExecutionEvent(
                project_id=project.id,
                action="project_created",
                message="Proyecto creado; pendiente de planificación.",
                previous_state=None,
                new_state=ProjectStatus.DRAFT.value,
                correlation_id=correlation_id,
            )
        )
        for approval in self.approval_policy.inspect_goal(project.id, cleaned):
            self.repository.add_approval(approval)
            self.repository.add_event(
                ExecutionEvent(
                    project_id=project.id,
                    action="approval_requested",
                    message=approval.reason,
                    new_state=ApprovalStatus.PENDING.value,
                    correlation_id=correlation_id,
                )
            )
        return project

    async def run_project(self, project_id: str) -> Project:
        return await self.orchestrator.run_project(project_id)

    async def plan_project(self, project_id: str) -> Project:
        return await self.orchestrator.plan_project(project_id)

    def pause_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        if project.status is not ProjectStatus.RUNNING:
            raise ValueError("Only a running project can be paused")
        return self.repository.update_project_status(project_id, ProjectStatus.PAUSED)

    def resume_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        pending = self.repository.list_approvals(project_id, ApprovalStatus.PENDING)
        if pending:
            raise ValueError("Resolve pending approvals before resuming")
        if project.status not in {
            ProjectStatus.PAUSED,
            ProjectStatus.AWAITING_APPROVAL,
            ProjectStatus.FAILED,
        }:
            raise ValueError("Project is not resumable")
        return self.repository.update_project_status(project_id, ProjectStatus.READY)

    def cancel_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}:
            return project
        for item in self.repository.list_work_items(project_id):
            if item.status not in {
                WorkItemStatus.COMPLETED,
                WorkItemStatus.CANCELLED,
            }:
                try:
                    self.repository.transition_work_item(item.id, WorkItemStatus.CANCELLED)
                except ValueError:
                    pass
        return self.repository.update_project_status(project_id, ProjectStatus.CANCELLED)

    def retry_work_item(self, work_item_id: str) -> WorkItem:
        item = self.repository.get_work_item(work_item_id)
        if item.attempt_count >= item.max_attempts:
            raise ValueError("Task attempt budget is exhausted")
        if item.status not in {
            WorkItemStatus.FAILED,
            WorkItemStatus.CHANGES_REQUESTED,
        }:
            raise ValueError("Only failed or rejected tasks can be retried")
        return self.repository.transition_work_item(work_item_id, WorkItemStatus.READY)

    def request_approval(
        self,
        project_id: str,
        action: str,
        reason: str,
        *,
        work_item_id: str | None = None,
        affected_resources: list[str] | None = None,
    ) -> ApprovalRequest:
        self.repository.get_project(project_id)
        approval = ApprovalRequest(
            project_id=project_id,
            work_item_id=work_item_id,
            action=action,
            reason=reason,
            risk="high",
            alternatives=["No ejecutar", "Preparar sólo un plan reversible"],
            affected_resources=affected_resources or [],
        )
        self.repository.add_approval(approval)
        return approval

    def escalate_work_item(self, work_item_id: str, reason: str) -> ApprovalRequest:
        item = self.repository.get_work_item(work_item_id)
        if item.status in {
            WorkItemStatus.COMPLETED,
            WorkItemStatus.CANCELLED,
        }:
            raise ValueError("A terminal task cannot be escalated")
        approval = self.request_approval(
            item.project_id,
            f"Escalar tarea: {item.title}",
            reason,
            work_item_id=item.id,
            affected_resources=item.authorized_files,
        )
        self.repository.update_project_status(item.project_id, ProjectStatus.AWAITING_APPROVAL)
        self.repository.add_event(
            ExecutionEvent(
                project_id=item.project_id,
                work_item_id=item.id,
                action="task_escalated",
                message=reason,
                new_state=ProjectStatus.AWAITING_APPROVAL.value,
            )
        )
        return approval

    def project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "milestones": [
                value.model_dump(mode="json")
                for value in self.repository.list_milestones(project_id)
            ],
            "work_items": [
                value.model_dump(mode="json")
                for value in self.repository.list_work_items(project_id)
            ],
            "dependencies": [
                value.model_dump(mode="json")
                for value in self.repository.list_dependencies(project_id)
            ],
            "artifacts": [
                value.model_dump(mode="json")
                for value in self.repository.list_artifacts(project_id)
            ],
            "reviews": [
                value.model_dump(mode="json") for value in self.repository.list_reviews(project_id)
            ],
            "test_reports": [
                value.model_dump(mode="json")
                for value in self.repository.list_test_reports(project_id)
            ],
            "decisions": [
                value.model_dump(mode="json")
                for value in self.repository.list_decisions(project_id)
            ],
            "approvals": [
                value.model_dump(mode="json")
                for value in self.repository.list_approvals(project_id)
            ],
            "metrics": self.repository.metrics(project_id),
        }

    @staticmethod
    def _title(goal: str) -> str:
        words = goal.replace("\n", " ").split()
        title = " ".join(words[:9])
        return title[:80] + ("…" if len(words) > 9 else "")


def build_application(
    settings: Settings | None = None,
    database: Database | None = None,
) -> ApplicationService:
    resolved_settings = settings or get_settings()
    resolved_settings.ensure_directories()
    resolved_database = database or Database(resolved_settings.resolved_database_url())
    repository = Repository(resolved_database)
    scheduler = ResourceScheduler(resolved_settings.model_concurrency)
    catalog = RoleCatalog(Path(resolved_settings.config_root) / "roles" / "default.yaml")
    providers = ProviderRegistry(resolved_settings)
    runner = RoleRunner(catalog, providers, scheduler)
    memory = ContextBuilder(repository)
    artifact_store = ArtifactStore(resolved_settings.workspace_root)
    orchestrator = Orchestrator(repository, runner, artifact_store, memory)
    return ApplicationService(
        resolved_settings,
        resolved_database,
        repository,
        orchestrator,
        scheduler,
        catalog,
    )
