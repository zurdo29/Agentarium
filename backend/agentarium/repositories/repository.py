from __future__ import annotations

import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from agentarium.domain.enums import (
    AgentRole,
    ApprovalStatus,
    OutputStrategy,
    ProjectStatus,
    ReviewVerdict,
    RiskLevel,
    RunOutcome,
    WorkItemStatus,
)
from agentarium.domain.models import (
    MAX_WORK_ITEM_ATTEMPTS,
    AgentRun,
    ApprovalRequest,
    Artifact,
    Decision,
    Dependency,
    ExecutionEvent,
    Milestone,
    Project,
    ProjectBrief,
    ResourceUsage,
    Review,
    ScriptExecutionContract,
    TestReport,
    WorkItem,
    new_id,
    utc_now,
)
from agentarium.domain.state_machine import validate_transition

from .database import Database
from .tables import (
    AgentRunRow,
    ApprovalRow,
    ArtifactRow,
    DecisionRow,
    DependencyRow,
    EventRow,
    MilestoneRow,
    ProjectRow,
    ReviewRow,
    TestReportRow,
    WorkItemRow,
)


class NotFoundError(LookupError):
    pass


class ConcurrentModificationError(RuntimeError):
    pass


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_project(self, project: Project) -> Project:
        with self.database.session() as session:
            session.add(
                ProjectRow(
                    id=project.id,
                    title=project.title,
                    goal=project.goal,
                    status=project.status.value,
                    brief_json=None,
                    current_milestone_id=project.current_milestone_id,
                    progress_percent=project.progress_percent,
                    imported=project.imported,
                    imported_source_path=project.imported_source_path,
                    imported_commit=project.imported_commit,
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                )
            )
        return project

    def get_project(self, project_id: str) -> Project:
        with self.database.session() as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise NotFoundError(f"Project {project_id} not found")
            return self._project_from_row(row)

    def list_projects(self) -> list[Project]:
        with self.database.session() as session:
            rows = session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc())).all()
            return [self._project_from_row(row) for row in rows]

    def update_project_status(self, project_id: str, status: ProjectStatus) -> Project:
        with self.database.session() as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise NotFoundError(f"Project {project_id} not found")
            row.status = status.value
            row.updated_at = utc_now()
        return self.get_project(project_id)

    def set_project_brief(self, brief: ProjectBrief) -> None:
        with self.database.session() as session:
            row = session.get(ProjectRow, brief.project_id)
            if row is None:
                raise NotFoundError(f"Project {brief.project_id} not found")
            row.brief_json = brief.model_dump(mode="json")
            row.updated_at = utc_now()

    def update_project_progress(
        self,
        project_id: str,
        progress_percent: float,
        current_milestone_id: str | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise NotFoundError(f"Project {project_id} not found")
            row.progress_percent = progress_percent
            if current_milestone_id is not None:
                row.current_milestone_id = current_milestone_id
            row.updated_at = utc_now()

    def add_milestone(self, milestone: Milestone) -> None:
        with self.database.session() as session:
            session.add(
                MilestoneRow(
                    id=milestone.id,
                    project_id=milestone.project_id,
                    title=milestone.title,
                    description=milestone.description,
                    order=milestone.order,
                    completed=milestone.completed,
                    created_at=milestone.created_at,
                )
            )

    def list_milestones(self, project_id: str) -> list[Milestone]:
        with self.database.session() as session:
            rows = session.scalars(
                select(MilestoneRow)
                .where(MilestoneRow.project_id == project_id)
                .order_by(MilestoneRow.order)
            ).all()
            return [
                Milestone(
                    id=row.id,
                    project_id=row.project_id,
                    title=row.title,
                    description=row.description,
                    order=row.order,
                    completed=row.completed,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_work_item(self, item: WorkItem) -> None:
        with self.database.session() as session:
            session.add(
                WorkItemRow(
                    id=item.id,
                    project_id=item.project_id,
                    milestone_id=item.milestone_id,
                    title=item.title,
                    description=item.description,
                    assignee_role=item.assignee_role.value,
                    inputs_json=item.inputs,
                    expected_outputs_json=item.expected_outputs,
                    acceptance_criteria_json=item.acceptance_criteria,
                    allowed_tools_json=item.allowed_tools,
                    authorized_files_json=item.authorized_files,
                    max_attempts=item.max_attempts,
                    attempt_count=item.attempt_count,
                    risk=item.risk.value,
                    requires_approval=item.requires_approval,
                    priority=item.priority,
                    status=item.status.value,
                    last_error=item.last_error,
                    version=item.version,
                    owned_paths_json=item.owned_paths,
                    shared_component=item.shared_component,
                    output_strategy=item.output_strategy.value,
                    split_depth=item.split_depth,
                    execution_contract_json=(
                        item.execution_contract.model_dump(mode="json")
                        if item.execution_contract
                        else None
                    ),
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
            # The dependency table has a self-referential FK to work_items.
            # Flush the task first so SQLite can verify both endpoints.
            session.flush()
            for dependency_id in item.dependency_ids:
                session.add(
                    DependencyRow(
                        id=new_id(),
                        project_id=item.project_id,
                        work_item_id=item.id,
                        depends_on_id=dependency_id,
                    )
                )

    def get_work_item(self, item_id: str) -> WorkItem:
        with self.database.session() as session:
            row = session.get(WorkItemRow, item_id)
            if row is None:
                raise NotFoundError(f"Work item {item_id} not found")
            dependency_ids = list(
                session.scalars(
                    select(DependencyRow.depends_on_id).where(DependencyRow.work_item_id == item_id)
                )
            )
            return self._work_item_from_row(row, dependency_ids)

    def list_work_items(self, project_id: str) -> list[WorkItem]:
        with self.database.session() as session:
            rows = session.scalars(
                select(WorkItemRow)
                .where(WorkItemRow.project_id == project_id)
                .order_by(WorkItemRow.priority.desc(), WorkItemRow.created_at)
            ).all()
            dependencies = session.scalars(
                select(DependencyRow).where(DependencyRow.project_id == project_id)
            ).all()
            by_item: dict[str, list[str]] = {}
            for dependency in dependencies:
                by_item.setdefault(dependency.work_item_id, []).append(dependency.depends_on_id)
            return [self._work_item_from_row(row, by_item.get(row.id, [])) for row in rows]

    def list_dependencies(self, project_id: str) -> list[Dependency]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DependencyRow).where(DependencyRow.project_id == project_id)
            ).all()
            return [
                Dependency(
                    id=row.id,
                    project_id=row.project_id,
                    work_item_id=row.work_item_id,
                    depends_on_id=row.depends_on_id,
                )
                for row in rows
            ]

    def retarget_dependency(
        self,
        project_id: str,
        old_depends_on_id: str,
        new_depends_on_id: str,
    ) -> None:
        with self.database.session() as session:
            rows = session.scalars(
                select(DependencyRow).where(
                    DependencyRow.project_id == project_id,
                    DependencyRow.depends_on_id == old_depends_on_id,
                )
            ).all()
            for row in rows:
                owner = session.get(WorkItemRow, row.work_item_id)
                if owner is not None and owner.status != WorkItemStatus.BLOCKED.value:
                    raise ValueError(
                        "Cannot retarget a dependency for a work item that is "
                        f"not BLOCKED: {row.work_item_id} is {owner.status}"
                    )
                row.depends_on_id = new_depends_on_id

    _MAX_TRANSITION_RETRIES = 5

    def transition_work_item(
        self,
        item_id: str,
        target: WorkItemStatus,
        *,
        error: str | None = None,
        event: ExecutionEvent | None = None,
    ) -> WorkItem:
        for attempt in range(self._MAX_TRANSITION_RETRIES):
            with self.database.session() as session:
                row = session.get(WorkItemRow, item_id)
                if row is None:
                    raise NotFoundError(f"Work item {item_id} not found")
                current = WorkItemStatus(row.status)
                validate_transition(current, target)
                expected_version = row.version
                cas_update = (
                    update(WorkItemRow)
                    .where(
                        WorkItemRow.id == item_id,
                        WorkItemRow.version == expected_version,
                    )
                    .values(
                        status=target.value,
                        last_error=error,
                        updated_at=utc_now(),
                        version=WorkItemRow.version + 1,
                    )
                    .execution_options(synchronize_session=False)
                )
                result = cast(CursorResult[Any], session.execute(cas_update))
                won_race = result.rowcount > 0
                if won_race and event is not None:
                    session.add(
                        EventRow(
                            id=event.id,
                            project_id=event.project_id,
                            work_item_id=event.work_item_id,
                            agent_run_id=event.agent_run_id,
                            agent_role=(
                                event.agent_role.value if event.agent_role else None
                            ),
                            model=event.model,
                            action=event.action,
                            message=event.message,
                            previous_state=event.previous_state,
                            new_state=event.new_state,
                            attempt=event.attempt,
                            error=event.error,
                            resource_usage_json=(
                                event.resource_usage.model_dump(mode="json")
                                if event.resource_usage
                                else None
                            ),
                            correlation_id=event.correlation_id,
                            metadata_json=event.metadata,
                            timestamp=event.timestamp,
                        )
                    )
            if won_race:
                return self.get_work_item(item_id)
            time.sleep(0.01 * (attempt + 1))
        raise ConcurrentModificationError(
            f"Work item {item_id} changed concurrently after "
            f"{self._MAX_TRANSITION_RETRIES} attempts"
        )

    def increment_attempt(self, item_id: str) -> WorkItem:
        with self.database.session() as session:
            row = session.get(WorkItemRow, item_id)
            if row is None:
                raise NotFoundError(f"Work item {item_id} not found")
            row.attempt_count += 1
            row.updated_at = utc_now()
        return self.get_work_item(item_id)

    def extend_attempt_budget(self, item_id: str, *, extra_attempts: int = 1) -> WorkItem:
        if extra_attempts < 1:
            raise ValueError("Attempt budget extension must be positive")
        with self.database.session() as session:
            row = session.get(WorkItemRow, item_id)
            if row is None:
                raise NotFoundError(f"Work item {item_id} not found")
            if row.max_attempts + extra_attempts > MAX_WORK_ITEM_ATTEMPTS:
                raise ValueError(
                    "Task attempt budget cannot exceed "
                    f"{MAX_WORK_ITEM_ATTEMPTS}"
                )
            row.max_attempts += extra_attempts
            row.updated_at = utc_now()
        return self.get_work_item(item_id)

    def update_priority(self, item_id: str, priority: int) -> WorkItem:
        with self.database.session() as session:
            row = session.get(WorkItemRow, item_id)
            if row is None:
                raise NotFoundError(f"Work item {item_id} not found")
            row.priority = priority
            row.updated_at = utc_now()
        return self.get_work_item(item_id)

    def add_agent_run(self, run: AgentRun) -> None:
        with self.database.session() as session:
            session.add(
                AgentRunRow(
                    id=run.id,
                    project_id=run.project_id,
                    work_item_id=run.work_item_id,
                    agent_role=run.agent_role.value,
                    model=run.model,
                    provider=run.provider,
                    attempt=run.attempt,
                    outcome=run.outcome.value,
                    input_summary=run.input_summary,
                    output_summary=run.output_summary,
                    resource_usage_json=run.resource_usage.model_dump(mode="json"),
                    correlation_id=run.correlation_id,
                    error=run.error,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                )
            )

    def list_agent_runs(self, project_id: str) -> list[AgentRun]:
        """Project-scoped, like `list_reviews`/`list_test_reports` --
        `list_events_for_work_item` is the one real exception to that
        convention and its own docstring names the reason (a hot path
        called on every attempt of every work item); nothing here is
        called anywhere near that often, a human opens this occasionally.
        Ordered by `started_at` with `id` as an explicit tie-breaker: the
        mock provider can produce several runs with an identical
        millisecond timestamp, and `list_reviews`/`list_test_reports`'s
        own single-column `order_by` would leave those in whatever order
        SQLite happens to return them in -- P4.4's attempt history needs
        to be reproducible, not just usually-stable."""
        with self.database.session() as session:
            rows = session.scalars(
                select(AgentRunRow)
                .where(AgentRunRow.project_id == project_id)
                .order_by(AgentRunRow.started_at, AgentRunRow.id)
            ).all()
            return [
                AgentRun(
                    id=row.id,
                    project_id=row.project_id,
                    work_item_id=row.work_item_id,
                    agent_role=AgentRole(row.agent_role),
                    model=row.model,
                    provider=row.provider,
                    attempt=row.attempt,
                    outcome=RunOutcome(row.outcome),
                    input_summary=row.input_summary,
                    output_summary=row.output_summary,
                    resource_usage=ResourceUsage(**row.resource_usage_json),
                    correlation_id=row.correlation_id,
                    error=row.error,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                )
                for row in rows
            ]

    def add_artifact(self, artifact: Artifact) -> None:
        with self.database.session() as session:
            session.add(
                ArtifactRow(
                    id=artifact.id,
                    project_id=artifact.project_id,
                    work_item_id=artifact.work_item_id,
                    agent_run_id=artifact.agent_run_id,
                    artifact_type=artifact.artifact_type,
                    title=artifact.title,
                    content_json=artifact.content,
                    file_paths_json=artifact.file_paths,
                    schema_version=artifact.schema_version,
                    checksum=artifact.checksum,
                    created_at=artifact.created_at,
                )
            )

    def update_artifact_file_paths(
        self,
        artifact_id: str,
        file_paths: list[str],
    ) -> None:
        with self.database.session() as session:
            row = session.get(ArtifactRow, artifact_id)
            if row is None:
                raise NotFoundError(f"Artifact not found: {artifact_id}")
            row.file_paths_json = file_paths

    def list_artifacts(self, project_id: str) -> list[Artifact]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ArtifactRow)
                .where(ArtifactRow.project_id == project_id)
                .order_by(ArtifactRow.created_at)
            ).all()
            return [
                Artifact(
                    id=row.id,
                    project_id=row.project_id,
                    work_item_id=row.work_item_id,
                    agent_run_id=row.agent_run_id,
                    artifact_type=row.artifact_type,
                    title=row.title,
                    content=row.content_json,
                    file_paths=row.file_paths_json,
                    schema_version=row.schema_version,
                    checksum=row.checksum,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_review(self, review: Review) -> None:
        with self.database.session() as session:
            session.add(
                ReviewRow(
                    id=review.id,
                    project_id=review.project_id,
                    work_item_id=review.work_item_id,
                    artifact_id=review.artifact_id,
                    reviewer_run_id=review.reviewer_run_id,
                    verdict=review.verdict.value,
                    reasons_json=review.reasons,
                    acceptance_results_json=review.acceptance_results,
                    created_at=review.created_at,
                )
            )

    def list_reviews(self, project_id: str) -> list[Review]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ReviewRow)
                .where(ReviewRow.project_id == project_id)
                .order_by(ReviewRow.created_at)
            ).all()
            return [
                Review(
                    id=row.id,
                    project_id=row.project_id,
                    work_item_id=row.work_item_id,
                    artifact_id=row.artifact_id,
                    reviewer_run_id=row.reviewer_run_id,
                    verdict=ReviewVerdict(row.verdict),
                    reasons=row.reasons_json,
                    acceptance_results=row.acceptance_results_json,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_test_report(self, report: TestReport) -> None:
        with self.database.session() as session:
            session.add(
                TestReportRow(
                    id=report.id,
                    project_id=report.project_id,
                    work_item_id=report.work_item_id,
                    artifact_id=report.artifact_id,
                    tester_run_id=report.tester_run_id,
                    passed=report.passed,
                    checks_json=report.checks,
                    command_evidence_json=report.command_evidence,
                    summary=report.summary,
                    created_at=report.created_at,
                )
            )

    def list_test_reports(self, project_id: str) -> list[TestReport]:
        with self.database.session() as session:
            rows = session.scalars(
                select(TestReportRow)
                .where(TestReportRow.project_id == project_id)
                .order_by(TestReportRow.created_at)
            ).all()
            return [
                TestReport(
                    id=row.id,
                    project_id=row.project_id,
                    work_item_id=row.work_item_id,
                    artifact_id=row.artifact_id,
                    tester_run_id=row.tester_run_id,
                    passed=row.passed,
                    checks=row.checks_json,
                    command_evidence=row.command_evidence_json,
                    summary=row.summary,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_decision(self, decision: Decision) -> None:
        with self.database.session() as session:
            session.add(
                DecisionRow(
                    id=decision.id,
                    project_id=decision.project_id,
                    title=decision.title,
                    decision=decision.decision,
                    rationale=decision.rationale,
                    reversible=decision.reversible,
                    alternatives_json=decision.alternatives,
                    created_at=decision.created_at,
                )
            )

    def list_decisions(self, project_id: str) -> list[Decision]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DecisionRow)
                .where(DecisionRow.project_id == project_id)
                .order_by(DecisionRow.created_at)
            ).all()
            return [
                Decision(
                    id=row.id,
                    project_id=row.project_id,
                    title=row.title,
                    decision=row.decision,
                    rationale=row.rationale,
                    reversible=row.reversible,
                    alternatives=row.alternatives_json,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_approval(self, approval: ApprovalRequest) -> None:
        with self.database.session() as session:
            session.add(
                ApprovalRow(
                    id=approval.id,
                    project_id=approval.project_id,
                    work_item_id=approval.work_item_id,
                    action=approval.action,
                    reason=approval.reason,
                    risk=approval.risk.value,
                    alternatives_json=approval.alternatives,
                    affected_resources_json=approval.affected_resources,
                    status=approval.status.value,
                    comments=approval.comments,
                    created_at=approval.created_at,
                    resolved_at=approval.resolved_at,
                )
            )

    def list_approvals(
        self,
        project_id: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        with self.database.session() as session:
            statement = select(ApprovalRow)
            if project_id:
                statement = statement.where(ApprovalRow.project_id == project_id)
            if status:
                statement = statement.where(ApprovalRow.status == status.value)
            rows = session.scalars(statement.order_by(ApprovalRow.created_at.desc())).all()
            return [self._approval_from_row(row) for row in rows]

    def resolve_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        comments: str | None,
    ) -> ApprovalRequest:
        if status is ApprovalStatus.PENDING:
            raise ValueError("A resolved approval cannot remain pending")
        with self.database.session() as session:
            row = session.get(ApprovalRow, approval_id)
            if row is None:
                raise NotFoundError(f"Approval {approval_id} not found")
            if row.status != ApprovalStatus.PENDING.value:
                raise ValueError("Approval has already been resolved")
            row.status = status.value
            row.comments = comments
            row.resolved_at = datetime.now(UTC)
        with self.database.session() as session:
            resolved = session.get(ApprovalRow, approval_id)
            if resolved is None:
                raise NotFoundError(f"Approval {approval_id} not found")
            return self._approval_from_row(resolved)

    def add_event(self, event: ExecutionEvent) -> None:
        with self.database.session() as session:
            session.add(
                EventRow(
                    id=event.id,
                    project_id=event.project_id,
                    work_item_id=event.work_item_id,
                    agent_run_id=event.agent_run_id,
                    agent_role=event.agent_role.value if event.agent_role else None,
                    model=event.model,
                    action=event.action,
                    message=event.message,
                    previous_state=event.previous_state,
                    new_state=event.new_state,
                    attempt=event.attempt,
                    error=event.error,
                    resource_usage_json=(
                        event.resource_usage.model_dump(mode="json")
                        if event.resource_usage
                        else None
                    ),
                    correlation_id=event.correlation_id,
                    metadata_json=event.metadata,
                    timestamp=event.timestamp,
                )
            )

    def list_events(
        self,
        project_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(EventRow)
                .where(
                    EventRow.project_id == project_id,
                    EventRow.sequence > after_sequence,
                )
                .order_by(EventRow.sequence)
                .limit(limit)
            ).all()
            return [self._event_from_row(row) for row in rows]

    def list_events_for_work_item(
        self,
        project_id: str,
        work_item_id: str,
        *,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        """The `limit` most recent events for one work item, chronological.

        Ordered `DESC` then reversed in Python, not `ASC` with a plain
        `LIMIT` — an ascending order with a cap keeps the *oldest* `limit`
        rows once a work item has more than `limit` events, which is
        backwards for retry context (P2.2/ADR 0029): a worker needs to see
        what happened most recently, not what happened first.

        A separate method rather than filtering `list_events(project_id)`
        in Python (the pattern `list_reviews`/`list_test_reports` already
        use): `ContextBuilder.operational()` calls this on every attempt of
        every work item — a hot path — and pulling every event in the
        project to discard most of them in Python does not scale the same
        way filtering `reviews`/`reports` does, where the per-project volume
        is much smaller. `EventRow.work_item_id` is already indexed.
        """
        with self.database.session() as session:
            rows = session.scalars(
                select(EventRow)
                .where(
                    EventRow.project_id == project_id,
                    EventRow.work_item_id == work_item_id,
                )
                .order_by(EventRow.sequence.desc())
                .limit(limit)
            ).all()
            return [self._event_from_row(row) for row in reversed(rows)]

    def recover_interrupted(self, project_id: str | None = None) -> int:
        """Reset work items stuck mid-flight (ASSIGNED/RUNNING/
        AWAITING_REVIEW) back to READY. Only safe to call for a project that
        is NOT currently being driven by a live `run_project` loop in some
        other process — callers must scope this deliberately (global, at
        real process startup; or per-project, right before that project's
        own run begins) rather than on every incidental DB access, or it
        will yank an actively-running item out from under its own
        orchestrator."""
        recovered = 0
        with self.database.session() as session:
            stuck_status = WorkItemRow.status.in_(
                [
                    WorkItemStatus.ASSIGNED.value,
                    WorkItemStatus.RUNNING.value,
                    WorkItemStatus.AWAITING_REVIEW.value,
                ]
            )
            query = (
                select(WorkItemRow).where(stuck_status, WorkItemRow.project_id == project_id)
                if project_id is not None
                else select(WorkItemRow).where(stuck_status)
            )
            rows = session.scalars(query).all()
            for row in rows:
                row.status = WorkItemStatus.READY.value
                row.last_error = "Recovered after interrupted execution"
                row.version += 1
                row.updated_at = utc_now()
                recovered += 1
        return recovered

    def metrics(self, project_id: str) -> dict[str, Any]:
        items = self.list_work_items(project_id)
        reviews = self.list_reviews(project_id)
        reports = self.list_test_reports(project_id)
        events = self.list_events(project_id, limit=5000)
        statuses = Counter(item.status.value for item in items)
        durations = [
            event["resource_usage"]["duration_ms"]
            for event in events
            if event["resource_usage"] is not None
        ]
        project = self.get_project(project_id)
        elapsed_ms = max(
            0,
            int((project.updated_at - project.created_at).total_seconds() * 1000),
        )
        return {
            "tasks_total": len(items),
            "tasks_completed": statuses[WorkItemStatus.COMPLETED.value],
            "tasks_rejected": sum(1 for event in events if event["action"] == "review_rejected"),
            "retries": sum(max(0, item.attempt_count - 1) for item in items),
            "blocked": statuses[WorkItemStatus.BLOCKED.value],
            "human_interventions": len(self.list_approvals(project_id, ApprovalStatus.APPROVED))
            + len(self.list_approvals(project_id, ApprovalStatus.REJECTED)),
            "tester_approval_rate": (
                sum(1 for report in reports if report.passed) / len(reports) if reports else 0
            ),
            "reviewer_approval_rate": (
                sum(1 for review in reviews if review.verdict is ReviewVerdict.APPROVED)
                / len(reviews)
                if reviews
                else 0
            ),
            "average_agent_duration_ms": (
                round(sum(durations) / len(durations), 2) if durations else 0
            ),
            "total_project_time_ms": elapsed_ms,
            "model_errors": sum(1 for event in events if event["error"]),
        }

    @staticmethod
    def _project_from_row(row: ProjectRow) -> Project:
        brief = ProjectBrief.model_validate(row.brief_json) if row.brief_json else None
        return Project(
            id=row.id,
            title=row.title,
            goal=row.goal,
            status=ProjectStatus(row.status),
            brief=brief,
            current_milestone_id=row.current_milestone_id,
            progress_percent=row.progress_percent,
            imported=row.imported,
            imported_source_path=row.imported_source_path,
            imported_commit=row.imported_commit,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _work_item_from_row(row: WorkItemRow, dependency_ids: list[str]) -> WorkItem:
        return WorkItem(
            id=row.id,
            project_id=row.project_id,
            milestone_id=row.milestone_id,
            title=row.title,
            description=row.description,
            assignee_role=AgentRole(row.assignee_role),
            inputs=row.inputs_json,
            expected_outputs=row.expected_outputs_json,
            dependency_ids=dependency_ids,
            acceptance_criteria=row.acceptance_criteria_json,
            allowed_tools=row.allowed_tools_json,
            authorized_files=row.authorized_files_json,
            max_attempts=row.max_attempts,
            attempt_count=row.attempt_count,
            risk=RiskLevel(row.risk),
            requires_approval=row.requires_approval,
            priority=row.priority,
            status=WorkItemStatus(row.status),
            last_error=row.last_error,
            version=row.version,
            owned_paths=row.owned_paths_json,
            shared_component=row.shared_component,
            output_strategy=OutputStrategy(row.output_strategy),
            split_depth=row.split_depth,
            execution_contract=(
                ScriptExecutionContract.model_validate(row.execution_contract_json)
                if row.execution_contract_json
                else None
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _approval_from_row(row: ApprovalRow) -> ApprovalRequest:
        return ApprovalRequest(
            id=row.id,
            project_id=row.project_id,
            work_item_id=row.work_item_id,
            action=row.action,
            reason=row.reason,
            risk=RiskLevel(row.risk),
            alternatives=row.alternatives_json,
            affected_resources=row.affected_resources_json,
            status=ApprovalStatus(row.status),
            comments=row.comments,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )

    @staticmethod
    def _event_from_row(row: EventRow) -> dict[str, Any]:
        usage = (
            ResourceUsage.model_validate(row.resource_usage_json).model_dump(mode="json")
            if row.resource_usage_json
            else None
        )
        return {
            "sequence": row.sequence,
            "id": row.id,
            "project_id": row.project_id,
            "work_item_id": row.work_item_id,
            "agent_run_id": row.agent_run_id,
            "agent_role": row.agent_role,
            "model": row.model,
            "action": row.action,
            "message": row.message,
            "previous_state": row.previous_state,
            "new_state": row.new_state,
            "attempt": row.attempt,
            "error": row.error,
            "resource_usage": usage,
            "correlation_id": row.correlation_id,
            "metadata": row.metadata_json,
            "timestamp": row.timestamp.isoformat(),
        }
