from __future__ import annotations

import asyncio
from typing import Any

from agentarium.agents.roles import RoleExecutionError, RoleRunner
from agentarium.artifacts import ArtifactStore
from agentarium.domain.enums import (
    AgentRole,
    ApprovalStatus,
    ProjectStatus,
    ReviewVerdict,
    RiskLevel,
    WorkItemStatus,
)
from agentarium.domain.models import (
    Artifact,
    Decision,
    ExecutionEvent,
    Milestone,
    Project,
    ProjectBrief,
    Review,
    TestReport,
    WorkItem,
    new_id,
)
from agentarium.llm import ModelRequest, ProviderResponse
from agentarium.memory import ContextBuilder
from agentarium.repositories import Repository

TERMINAL_TASK_STATES = {
    WorkItemStatus.COMPLETED,
    WorkItemStatus.FAILED,
    WorkItemStatus.CANCELLED,
}


class InvalidPlan(ValueError):
    pass


class Orchestrator:
    def __init__(
        self,
        repository: Repository,
        roles: RoleRunner,
        artifacts: ArtifactStore,
        memory: ContextBuilder,
        *,
        max_cycles: int = 100,
    ) -> None:
        self.repository = repository
        self.roles = roles
        self.artifacts = artifacts
        self.memory = memory
        self.max_cycles = max_cycles

    async def plan_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        existing_items = self.repository.list_work_items(project_id)
        if project.brief is not None and existing_items:
            return project

        self.repository.update_project_status(project_id, ProjectStatus.PLANNING)
        correlation_id = new_id()
        self._event(
            project_id,
            "planning_started",
            "El director comenzó a estructurar el objetivo.",
            previous=project.status.value,
            new=ProjectStatus.PLANNING.value,
            correlation_id=correlation_id,
        )

        brief_response, _ = await self._call_role(
            AgentRole.DIRECTOR,
            ModelRequest(
                operation="brief",
                project_id=project_id,
                payload={"goal": project.goal, "title": project.title},
            ),
            correlation_id,
        )
        brief = ProjectBrief(project_id=project_id, **brief_response.content)
        self.repository.set_project_brief(brief)

        plan_response, _ = await self._call_role(
            AgentRole.TECHNICAL_MANAGER,
            ModelRequest(
                operation="plan",
                project_id=project_id,
                payload={"brief": brief.model_dump(mode="json")},
            ),
            correlation_id,
        )
        raw_tasks = self._validate_plan(plan_response.content)
        milestone_data = plan_response.content["milestone"]
        milestone = Milestone(
            project_id=project_id,
            title=str(milestone_data["title"]),
            description=str(milestone_data["description"]),
            order=0,
        )
        self.repository.add_milestone(milestone)
        key_to_id = {str(task["key"]): new_id() for task in raw_tasks}
        for raw in raw_tasks:
            dependency_ids = [key_to_id[str(key)] for key in raw["dependencies"]]
            status = WorkItemStatus.BLOCKED if dependency_ids else WorkItemStatus.READY
            item = WorkItem(
                id=key_to_id[str(raw["key"])],
                project_id=project_id,
                milestone_id=milestone.id,
                title=str(raw["title"]),
                description=str(raw["description"]),
                dependency_ids=dependency_ids,
                expected_outputs=[str(value) for value in raw["expected_outputs"]],
                acceptance_criteria=[str(value) for value in raw["acceptance_criteria"]],
                allowed_tools=["read_file", "write_file", "run_command"],
                authorized_files=[f"workspaces/{project_id}/**"],
                max_attempts=3,
                risk=RiskLevel(str(raw["risk"])),
                priority=int(raw["priority"]),
                status=WorkItemStatus.DRAFT,
            )
            self.repository.add_work_item(item)
            self._transition(item, status, correlation_id)

        self.repository.add_decision(
            Decision(
                project_id=project_id,
                title="Alcance inicial conservador",
                decision="Ejecutar un único hito vertical con tres entregables dependientes.",
                rationale="Es reversible, observable y adecuado para el presupuesto local.",
                reversible=True,
                alternatives=[
                    "Crear varios hitos en paralelo",
                    "Solicitar más detalles al usuario",
                ],
            )
        )
        pending = self.repository.list_approvals(project_id, ApprovalStatus.PENDING)
        final_status = ProjectStatus.AWAITING_APPROVAL if pending else ProjectStatus.READY
        self.repository.update_project_progress(project_id, 0, milestone.id)
        project = self.repository.update_project_status(project_id, final_status)
        self._event(
            project_id,
            "planning_completed",
            f"Brief y DAG creados con {len(raw_tasks)} tareas.",
            previous=ProjectStatus.PLANNING.value,
            new=final_status.value,
            correlation_id=correlation_id,
        )
        return project

    async def run_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        if project.status is ProjectStatus.DRAFT:
            project = await self.plan_project(project_id)
        if project.status in {ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}:
            return project
        pending = self.repository.list_approvals(project_id, ApprovalStatus.PENDING)
        if pending:
            return self.repository.update_project_status(
                project_id, ProjectStatus.AWAITING_APPROVAL
            )
        if project.status is ProjectStatus.PAUSED:
            return project

        correlation_id = new_id()
        self.repository.update_project_status(project_id, ProjectStatus.RUNNING)
        self._event(
            project_id,
            "project_run_started",
            "La ejecución del proyecto comenzó.",
            previous=project.status.value,
            new=ProjectStatus.RUNNING.value,
            correlation_id=correlation_id,
        )

        for _ in range(self.max_cycles):
            current_project = self.repository.get_project(project_id)
            if current_project.status in {
                ProjectStatus.PAUSED,
                ProjectStatus.CANCELLED,
                ProjectStatus.AWAITING_APPROVAL,
            }:
                return current_project

            self._promote_unblocked(project_id, correlation_id)
            items = self.repository.list_work_items(project_id)
            ready = [item for item in items if item.status is WorkItemStatus.READY]
            if ready:
                await asyncio.gather(
                    *(self._execute_work_item(item, correlation_id) for item in ready)
                )
                self._update_progress(project_id)
                continue

            if items and all(item.status is WorkItemStatus.COMPLETED for item in items):
                self.repository.update_project_progress(project_id, 100)
                result = self.repository.update_project_status(project_id, ProjectStatus.COMPLETED)
                self._event(
                    project_id,
                    "project_completed",
                    "Todos los entregables pasaron testing y revisión crítica.",
                    previous=ProjectStatus.RUNNING.value,
                    new=ProjectStatus.COMPLETED.value,
                    correlation_id=correlation_id,
                )
                return result
            if any(item.status is WorkItemStatus.FAILED for item in items):
                result = self.repository.update_project_status(project_id, ProjectStatus.FAILED)
                self._event(
                    project_id,
                    "project_failed",
                    "Al menos una tarea agotó su presupuesto de intentos.",
                    previous=ProjectStatus.RUNNING.value,
                    new=ProjectStatus.FAILED.value,
                    correlation_id=correlation_id,
                )
                return result
            if any(item.status is WorkItemStatus.AWAITING_APPROVAL for item in items):
                return self.repository.update_project_status(
                    project_id, ProjectStatus.AWAITING_APPROVAL
                )

            result = self.repository.update_project_status(project_id, ProjectStatus.FAILED)
            self._event(
                project_id,
                "dag_deadlock",
                "No hay tareas listas y el DAG no puede progresar.",
                previous=ProjectStatus.RUNNING.value,
                new=ProjectStatus.FAILED.value,
                correlation_id=correlation_id,
            )
            return result

        result = self.repository.update_project_status(project_id, ProjectStatus.FAILED)
        self._event(
            project_id,
            "cycle_budget_exhausted",
            f"Se alcanzó el límite de {self.max_cycles} ciclos.",
            previous=ProjectStatus.RUNNING.value,
            new=ProjectStatus.FAILED.value,
            correlation_id=correlation_id,
        )
        return result

    async def _execute_work_item(
        self,
        original_item: WorkItem,
        correlation_id: str,
    ) -> None:
        item = self._transition(
            original_item,
            WorkItemStatus.ASSIGNED,
            correlation_id,
        )
        item = self._transition(item, WorkItemStatus.RUNNING, correlation_id)
        item = self.repository.increment_attempt(item.id)
        operational_context = self.memory.operational(item)
        try:
            work_response, worker_run = await self._call_role(
                AgentRole.IMPLEMENTATION_WORKER,
                ModelRequest(
                    operation="work",
                    project_id=item.project_id,
                    work_item_id=item.id,
                    attempt=item.attempt_count,
                    payload=operational_context,
                ),
                correlation_id,
            )
        except RoleExecutionError as exc:
            if item.attempt_count < item.max_attempts:
                self._transition(
                    item,
                    WorkItemStatus.READY,
                    correlation_id,
                    error=str(exc),
                )
            else:
                self._transition(
                    item,
                    WorkItemStatus.FAILED,
                    correlation_id,
                    error=str(exc),
                )
            return

        path, checksum = self.artifacts.materialize(
            item.project_id,
            item.id,
            item.attempt_count,
            work_response.content,
        )
        artifact = Artifact(
            project_id=item.project_id,
            work_item_id=item.id,
            agent_run_id=worker_run.id,
            artifact_type=str(work_response.content["artifact_type"]),
            title=str(work_response.content["title"]),
            content=work_response.content,
            file_paths=[path],
            checksum=checksum,
        )
        self.repository.add_artifact(artifact)
        item = self._transition(
            self.repository.get_work_item(item.id),
            WorkItemStatus.AWAITING_REVIEW,
            correlation_id,
        )

        file_verified = self.artifacts.verify(path, checksum)
        test_response, tester_run = await self._call_role(
            AgentRole.TESTER,
            ModelRequest(
                operation="test",
                project_id=item.project_id,
                work_item_id=item.id,
                attempt=item.attempt_count,
                payload={
                    "artifact": artifact.content,
                    "file_verified": file_verified,
                    "acceptance_criteria": item.acceptance_criteria,
                },
            ),
            correlation_id,
        )
        report = TestReport(
            project_id=item.project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            tester_run_id=tester_run.id,
            passed=bool(test_response.content["passed"]),
            checks=list(test_response.content["checks"]),
            command_evidence=[
                {
                    "check": "materialized_file_checksum",
                    "path": path,
                    "verified": file_verified,
                }
            ],
            summary=str(test_response.content["summary"]),
        )
        self.repository.add_test_report(report)

        review_response, reviewer_run = await self._call_role(
            AgentRole.CRITICAL_REVIEWER,
            ModelRequest(
                operation="review",
                project_id=item.project_id,
                work_item_id=item.id,
                attempt=item.attempt_count,
                payload={
                    "artifact": artifact.content,
                    "acceptance_criteria": item.acceptance_criteria,
                    "test_report": report.model_dump(mode="json"),
                },
            ),
            correlation_id,
        )
        review = Review(
            project_id=item.project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            reviewer_run_id=reviewer_run.id,
            verdict=ReviewVerdict(str(review_response.content["verdict"])),
            reasons=[str(value) for value in review_response.content["reasons"]],
            acceptance_results={
                str(key): bool(value)
                for key, value in review_response.content["acceptance_results"].items()
            },
        )
        self.repository.add_review(review)

        if report.passed and review.verdict is ReviewVerdict.APPROVED:
            item = self._transition(item, WorkItemStatus.PASSED, correlation_id)
            self._transition(item, WorkItemStatus.COMPLETED, correlation_id)
            return

        self._event(
            item.project_id,
            "review_rejected",
            "; ".join(review.reasons),
            work_item_id=item.id,
            attempt=item.attempt_count,
            correlation_id=correlation_id,
        )
        item = self._transition(
            item,
            WorkItemStatus.CHANGES_REQUESTED,
            correlation_id,
            error="; ".join(review.reasons),
        )
        if item.attempt_count < item.max_attempts:
            self._transition(item, WorkItemStatus.READY, correlation_id)
        else:
            self._transition(item, WorkItemStatus.FAILED, correlation_id)

    async def _call_role(
        self,
        role: AgentRole,
        request: ModelRequest,
        correlation_id: str,
    ) -> tuple[ProviderResponse, Any]:
        try:
            response, run = await self.roles.run(
                role,
                request,
                correlation_id=correlation_id,
            )
        except RoleExecutionError as exc:
            self.repository.add_agent_run(exc.run)
            self._event(
                request.project_id,
                "agent_run_failed",
                str(exc),
                work_item_id=request.work_item_id,
                agent_run_id=exc.run.id,
                role=role,
                attempt=request.attempt,
                error=str(exc),
                correlation_id=correlation_id,
            )
            raise
        self.repository.add_agent_run(run)
        self._event(
            request.project_id,
            "agent_run_completed",
            f"{role.value} entregó un artefacto estructurado.",
            work_item_id=request.work_item_id,
            agent_run_id=run.id,
            role=role,
            model=run.model,
            attempt=request.attempt,
            resource_usage=run.resource_usage,
            correlation_id=correlation_id,
        )
        return response, run

    def _promote_unblocked(self, project_id: str, correlation_id: str) -> None:
        items = self.repository.list_work_items(project_id)
        by_id = {item.id: item for item in items}
        for item in items:
            if item.status is not WorkItemStatus.BLOCKED:
                continue
            if all(
                by_id[dependency_id].status is WorkItemStatus.COMPLETED
                for dependency_id in item.dependency_ids
            ):
                self._transition(item, WorkItemStatus.READY, correlation_id)

    def _update_progress(self, project_id: str) -> None:
        items = self.repository.list_work_items(project_id)
        completed = sum(item.status is WorkItemStatus.COMPLETED for item in items)
        percent = (completed / len(items) * 100) if items else 0
        self.repository.update_project_progress(project_id, percent)

    def _transition(
        self,
        item: WorkItem,
        target: WorkItemStatus,
        correlation_id: str,
        *,
        error: str | None = None,
    ) -> WorkItem:
        current = item.status
        updated = self.repository.transition_work_item(item.id, target, error=error)
        self._event(
            item.project_id,
            "task_state_changed",
            f"{item.title}: {current.value} → {target.value}",
            work_item_id=item.id,
            previous=current.value,
            new=target.value,
            attempt=updated.attempt_count,
            error=error,
            correlation_id=correlation_id,
        )
        return updated

    def _event(
        self,
        project_id: str,
        action: str,
        message: str,
        *,
        work_item_id: str | None = None,
        agent_run_id: str | None = None,
        role: AgentRole | None = None,
        model: str | None = None,
        previous: str | None = None,
        new: str | None = None,
        attempt: int | None = None,
        error: str | None = None,
        resource_usage: Any = None,
        correlation_id: str,
    ) -> None:
        self.repository.add_event(
            ExecutionEvent(
                project_id=project_id,
                work_item_id=work_item_id,
                agent_run_id=agent_run_id,
                agent_role=role,
                model=model,
                action=action,
                message=message,
                previous_state=previous,
                new_state=new,
                attempt=attempt,
                error=error,
                resource_usage=resource_usage,
                correlation_id=correlation_id,
            )
        )

    @staticmethod
    def _validate_plan(content: dict[str, Any]) -> list[dict[str, Any]]:
        if "milestone" not in content or "tasks" not in content:
            raise InvalidPlan("Plan must contain milestone and tasks")
        tasks = content["tasks"]
        if not isinstance(tasks, list) or not tasks:
            raise InvalidPlan("Plan must contain at least one task")
        keys = [str(task.get("key", "")) for task in tasks]
        if len(set(keys)) != len(keys) or any(not key for key in keys):
            raise InvalidPlan("Task keys must be non-empty and unique")
        titles = [str(task.get("title", "")).strip().casefold() for task in tasks]
        if len(set(titles)) != len(titles):
            raise InvalidPlan("Repeated task detected")
        known = set(keys)
        graph: dict[str, list[str]] = {}
        required = {
            "key",
            "title",
            "description",
            "dependencies",
            "expected_outputs",
            "acceptance_criteria",
            "risk",
            "priority",
        }
        for task in tasks:
            missing = required - set(task)
            if missing:
                raise InvalidPlan(f"Task is missing fields: {sorted(missing)}")
            dependencies = [str(value) for value in task["dependencies"]]
            if not set(dependencies).issubset(known):
                raise InvalidPlan("Task references an unknown dependency")
            graph[str(task["key"])] = dependencies
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise InvalidPlan("Task dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for key in keys:
            visit(key)
        return tasks
