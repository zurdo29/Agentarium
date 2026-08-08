from __future__ import annotations

import asyncio
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
from agentarium.execution import (
    ValidationProfileExecutor,
    WorkArtifactProposal,
    WorkspaceFileProposal,
    WorkspaceMaterializer,
    WorkspacePreview,
    build_runtime_capabilities,
)
from agentarium.execution.scheduler import ResourceScheduler
from agentarium.isolation import GitWorktreeIsolation, ImportSource, SourceInspection
from agentarium.llm import ProviderRegistry, ProviderSelection, ProviderSelectionStore
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
        providers: ProviderRegistry,
        provider_selection: ProviderSelectionStore,
        preview: WorkspacePreview,
        import_source: ImportSource,
    ) -> None:
        self.settings = settings
        self.database = database
        self.repository = repository
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.roles = roles
        self.providers = providers
        self.provider_selection = provider_selection
        self.preview = preview
        self.import_source = import_source
        self.approval_policy = ApprovalPolicy()

    def ensure_ready(self) -> None:
        """Idempotent, safe to call from any process on every invocation:
        directories + schema only, no crash recovery. Use this (not
        `initialize`) for anything that isn't a genuine one-time process
        startup — recovery must stay scoped to that, or a second process
        (e.g. a CLI read command) will reset work items an active
        `run_project` elsewhere is still mid-flight on."""
        self.settings.ensure_directories()
        self.database.create_all()

    def initialize(self) -> int:
        """Full startup: schema + a GLOBAL crash-recovery sweep. Call this
        once per real process lifetime (the API server's lifespan, or the
        explicit `agentarium init` command) — never from a per-command
        helper that runs on every CLI invocation."""
        self.ensure_ready()
        return self.repository.recover_interrupted()

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

    async def inspect_import_source(self, source_path: str) -> SourceInspection:
        return await self.import_source.inspect(Path(source_path))

    async def import_project(
        self, source_path: str, goal: str, title: str | None = None
    ) -> Project:
        """P4.1. Mirrors `create_project`'s shape (title generation, the
        same `project_created`-style event, the same `inspect_goal` call),
        but the id is generated up front and the copy happens before any
        row is persisted: if `import_source.import_into` fails partway,
        nothing is left behind with `imported=True` and no real content on
        disk. `inspect_goal` still runs unchanged -- its triggers are about
        the goal text, not about the fact of importing."""
        cleaned_goal = goal.strip()
        if not cleaned_goal:
            raise ValueError("Goal cannot be empty")
        generated_title = (
            title.strip() if title and title.strip() else self._title(cleaned_goal)
        )
        resolved_source = str(await asyncio.to_thread(Path(source_path).resolve))
        project_id = new_id()
        inspection = await self.import_source.import_into(Path(source_path), project_id)
        project = self.repository.create_project(
            Project(
                id=project_id,
                title=generated_title,
                goal=cleaned_goal,
                imported=True,
                imported_source_path=resolved_source,
                imported_commit=inspection.head_commit,
            )
        )
        correlation_id = new_id()
        self.repository.add_event(
            ExecutionEvent(
                project_id=project.id,
                action="project_imported",
                message=f"Proyecto importado desde {resolved_source}.",
                previous_state=None,
                new_state=ProjectStatus.DRAFT.value,
                correlation_id=correlation_id,
                metadata={
                    "source_path": resolved_source,
                    "commit": inspection.head_commit,
                    "branch": inspection.branch,
                },
            )
        )
        for approval in self.approval_policy.inspect_goal(project.id, cleaned_goal):
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
        recovered_exhausted_item = (
            item.status is WorkItemStatus.READY
            and item.attempt_count >= item.max_attempts
        )
        if item.status not in {
            WorkItemStatus.FAILED,
            WorkItemStatus.CHANGES_REQUESTED,
        } and not recovered_exhausted_item:
            raise ValueError("Only failed or rejected tasks can be retried")
        if item.attempt_count >= item.max_attempts:
            item = self.repository.extend_attempt_budget(item.id)
            self.repository.add_event(
                ExecutionEvent(
                    project_id=item.project_id,
                    work_item_id=item.id,
                    action="attempt_budget_extended",
                    message=(
                        "El usuario autorizó un intento adicional "
                        f"({item.max_attempts} máximo)."
                    ),
                    new_state=item.status.value,
                )
            )
        if item.status is WorkItemStatus.READY:
            return item
        return self.repository.transition_work_item(work_item_id, WorkItemStatus.READY)

    async def recover_artifact(
        self,
        work_item_id: str,
        artifact_id: str,
    ) -> WorkItem:
        item = self.repository.get_work_item(work_item_id)
        if item.status in {
            WorkItemStatus.FAILED,
            WorkItemStatus.CHANGES_REQUESTED,
        } or (
            item.status is WorkItemStatus.READY
            and item.attempt_count >= item.max_attempts
        ):
            item = self.retry_work_item(work_item_id)
        if item.status is not WorkItemStatus.READY:
            raise ValueError("Only a ready, failed or rejected task can recover an artifact")
        project = self.repository.get_project(item.project_id)
        if project.status is ProjectStatus.FAILED:
            self.repository.update_project_status(item.project_id, ProjectStatus.READY)
        return await self.orchestrator.re_evaluate_artifact(
            work_item_id,
            artifact_id,
        )

    async def submit_candidate(
        self,
        work_item_id: str,
        *,
        title: str,
        summary: str,
        files: list[dict[str, Any]],
    ) -> WorkItem:
        item = self.repository.get_work_item(work_item_id)
        if item.status in {
            WorkItemStatus.FAILED,
            WorkItemStatus.CHANGES_REQUESTED,
        } or (
            item.status is WorkItemStatus.READY
            and item.attempt_count >= item.max_attempts
        ):
            item = self.retry_work_item(work_item_id)
        if item.status is not WorkItemStatus.READY:
            raise ValueError("Only a ready, failed or rejected task accepts a candidate")
        project = self.repository.get_project(item.project_id)
        if project.status is ProjectStatus.FAILED:
            self.repository.update_project_status(item.project_id, ProjectStatus.READY)
        proposal = WorkArtifactProposal(
            artifact_type="Code",
            title=title,
            summary=summary,
            quality="verified",
            acceptance_criteria_addressed=item.acceptance_criteria,
            files=[
                WorkspaceFileProposal.model_validate(file)
                for file in files
            ],
        )
        return await self.orchestrator.evaluate_operator_candidate(
            work_item_id,
            proposal,
        )

    def rework_work_item(
        self,
        work_item_id: str,
        reason: str,
        acceptance_criteria: list[str] | None = None,
    ) -> WorkItem:
        source = self.repository.get_work_item(work_item_id)
        if source.status is not WorkItemStatus.COMPLETED:
            raise ValueError("Only completed tasks can start a rework revision")
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("Rework reason cannot be empty")
        extended_criteria = list(source.acceptance_criteria)
        for criterion in acceptance_criteria or []:
            cleaned_criterion = criterion.strip()
            if cleaned_criterion and cleaned_criterion not in extended_criteria:
                extended_criteria.append(cleaned_criterion)
        revision = WorkItem(
            project_id=source.project_id,
            milestone_id=source.milestone_id,
            title=f"Revisión: {source.title}",
            description=(
                f"{source.description}\n\nCorrección solicitada: {cleaned_reason}"
            ),
            inputs=[*source.inputs, f"Corrección solicitada: {cleaned_reason}"],
            expected_outputs=source.expected_outputs,
            dependency_ids=[source.id],
            acceptance_criteria=extended_criteria,
            allowed_tools=source.allowed_tools,
            authorized_files=source.authorized_files,
            max_attempts=3,
            risk=source.risk,
            requires_approval=source.requires_approval,
            priority=min(100, source.priority + 5),
            status=WorkItemStatus.READY,
        )
        self.repository.add_work_item(revision)
        items = self.repository.list_work_items(source.project_id)
        completed = sum(
            item.status is WorkItemStatus.COMPLETED
            for item in items
        )
        self.repository.update_project_progress(
            source.project_id,
            completed / len(items) * 100,
        )
        self.repository.update_project_status(source.project_id, ProjectStatus.READY)
        self.repository.add_event(
            ExecutionEvent(
                project_id=source.project_id,
                work_item_id=revision.id,
                action="task_rework_created",
                message=(
                    "Se abrió una revisión auditable de una entrega completada: "
                    f"{cleaned_reason}"
                ),
                previous_state=ProjectStatus.COMPLETED.value,
                new_state=ProjectStatus.READY.value,
            )
        )
        return revision

    async def provider_status(self, *, force: bool = False) -> dict[str, Any]:
        active_provider = self.roles.active_provider()
        active_model = self.roles.active_model()
        diagnostics = await self.providers.diagnostics(force=force)
        return {
            "active_provider": active_provider,
            "active_model": active_model,
            "providers": [
                {
                    **diagnostic.model_dump(mode="json"),
                    "selected": diagnostic.name == active_provider,
                    "selected_model": (
                        active_model if diagnostic.name == active_provider else None
                    ),
                }
                for diagnostic in diagnostics
            ],
        }

    async def select_provider(self, provider: str, model: str | None = None) -> dict[str, Any]:
        if self.scheduler.active:
            raise ValueError("No se puede cambiar el modelo mientras hay agentes activos")
        diagnostic = await self.providers.diagnostic(provider, force=True)
        if not diagnostic.ready:
            raise ValueError(diagnostic.message)
        if provider == "mock":
            selection = ProviderSelection(provider="mock")
            self.provider_selection.save(selection)
            self.roles.select_provider("mock")
        else:
            selected_model = (model or "").strip()
            if not selected_model:
                raise ValueError("Selecciona un modelo antes de activar el proveedor")
            if selected_model not in diagnostic.models:
                raise ValueError(
                    f"El modelo {selected_model!r} no está disponible en {diagnostic.label}"
                )
            selection = ProviderSelection(provider=provider, model=selected_model)
            self.provider_selection.save(selection)
            self.roles.select_provider(provider, selected_model)
        return await self.provider_status()

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
    provider_selection = ProviderSelectionStore(resolved_settings.provider_state_path)
    saved_selection = provider_selection.load()
    catalog.select_provider(
        saved_selection.provider if saved_selection else resolved_settings.provider,
        saved_selection.model if saved_selection else resolved_settings.model or None,
    )
    providers = ProviderRegistry(resolved_settings)
    runner = RoleRunner(catalog, providers, scheduler)
    capabilities = build_runtime_capabilities(
        Path(resolved_settings.config_root) / "policies" / "security.yaml"
    )
    memory = ContextBuilder(repository, capabilities)
    artifact_store = ArtifactStore(resolved_settings.workspace_root)
    workspace = WorkspaceMaterializer(
        resolved_settings.workspace_root,
        Path(resolved_settings.config_root) / "policies" / "security.yaml",
    )
    preview = WorkspacePreview(resolved_settings.workspace_root)
    validations = ValidationProfileExecutor(
        resolved_settings.workspace_root,
        Path(resolved_settings.config_root) / "policies" / "security.yaml",
        capabilities=capabilities,
    )
    isolation = GitWorktreeIsolation(
        resolved_settings.workspace_root,
        Path(resolved_settings.config_root) / "policies" / "security.yaml",
    )
    import_source = ImportSource(resolved_settings.workspace_root)
    orchestrator = Orchestrator(
        repository,
        runner,
        artifact_store,
        workspace,
        validations,
        isolation,
        memory,
    )
    return ApplicationService(
        resolved_settings,
        resolved_database,
        repository,
        orchestrator,
        scheduler,
        catalog,
        providers,
        provider_selection,
        preview,
        import_source,
    )
