from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from agentarium.agents.roles import RoleExecutionError, RoleRunner
from agentarium.artifacts import ArtifactStore
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
    AgentRun,
    Artifact,
    Decision,
    ExecutionEvent,
    Milestone,
    Project,
    ProjectBrief,
    ResourceUsage,
    Review,
    TestReport,
    WorkItem,
    new_id,
)
from agentarium.execution import (
    CommandRejected,
    ReviewEvaluationProposal,
    TestEvaluationProposal,
    ValidationProfileExecutor,
    WorkArtifactProposal,
    WorkspaceFileEvidence,
    WorkspaceInfrastructureRejected,
    WorkspaceMaterializer,
    WorkspaceRejected,
    WorkspaceSecurityRejected,
)
from agentarium.isolation import (
    GitChangeSet,
    GitWorktreeIsolation,
    IsolationError,
    WorktreeSession,
)
from agentarium.llm import ModelRequest, ProviderResponse
from agentarium.memory import ContextBuilder
from agentarium.planning import (
    BriefProposal,
    DecomposeProposal,
    PlanProposal,
    SubtaskProposal,
    TaskProposal,
    acceptance_criteria_index,
    merge_path_claims,
)
from agentarium.repositories import Repository

TERMINAL_TASK_STATES = {
    WorkItemStatus.COMPLETED,
    WorkItemStatus.FAILED,
    WorkItemStatus.CANCELLED,
}

DEPENDENCY_CONSISTENCY_CRITERION = (
    "El artefacto no contradice ni redefine de forma distinta datos, cifras "
    "o terminos ya establecidos en sus artefactos dependientes aprobados."
)


class InvalidPlan(ValueError):
    pass


class Orchestrator:
    # A planned task may be split once. Its children and their consolidation
    # inherit depth 1 and are terminal: see ADR 0021.
    MAX_SPLIT_DEPTH = 1

    def __init__(
        self,
        repository: Repository,
        roles: RoleRunner,
        artifacts: ArtifactStore,
        workspace: WorkspaceMaterializer,
        validations: ValidationProfileExecutor,
        isolation: GitWorktreeIsolation,
        memory: ContextBuilder,
        *,
        max_cycles: int = 100,
    ) -> None:
        self.repository = repository
        self.roles = roles
        self.artifacts = artifacts
        self.workspace = workspace
        self.validations = validations
        self.isolation = isolation
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

        try:
            brief_response, _ = await self._call_role(
                AgentRole.DIRECTOR,
                ModelRequest(
                    operation="brief",
                    project_id=project_id,
                    payload={"goal": project.goal, "title": project.title},
                ),
                correlation_id,
            )
        except RoleExecutionError as exc:
            return self._fail_planning(project_id, correlation_id, str(exc))
        try:
            brief_proposal = BriefProposal.model_validate(brief_response.content)
        except ValidationError as exc:
            raise InvalidPlan(f"Invalid project brief: {exc}") from exc
        brief = ProjectBrief(
            project_id=project_id,
            **brief_proposal.model_dump(mode="json"),
        )
        self.repository.set_project_brief(brief)

        try:
            plan_response, _ = await self._call_role(
                AgentRole.TECHNICAL_MANAGER,
                ModelRequest(
                    operation="plan",
                    project_id=project_id,
                    payload={
                        "brief": brief.model_dump(mode="json"),
                        "runtime_capabilities": self.memory.capabilities.model_dump(
                            mode="json"
                        ),
                    },
                ),
                correlation_id,
            )
        except RoleExecutionError as exc:
            return self._fail_planning(project_id, correlation_id, str(exc))
        plan = self._validate_plan(plan_response.content)
        plan, missing_deliverables, missing_scope, missing_success_criteria = (
            self._ensure_plan_covers_brief(plan, brief)
        )
        if missing_deliverables or missing_scope or missing_success_criteria:
            self._event(
                project_id,
                "plan_contract_completed",
                (
                    "Se añadió una tarea de cierre para cubrir el contrato "
                    "completo del brief."
                ),
                metadata={
                    "missing_deliverables": missing_deliverables,
                    "missing_scope": missing_scope,
                    "missing_success_criteria": missing_success_criteria,
                },
                correlation_id=correlation_id,
            )
        plan = await self._resolve_owned_path_conflicts(
            plan, brief, project_id, correlation_id
        )
        milestone = Milestone(
            project_id=project_id,
            title=plan.milestone.title,
            description=plan.milestone.description,
            order=0,
        )
        self.repository.add_milestone(milestone)
        key_to_id = {task.key: new_id() for task in plan.tasks}
        for task in plan.tasks:
            dependency_ids = [key_to_id[key] for key in task.dependencies]
            status = WorkItemStatus.BLOCKED if dependency_ids else WorkItemStatus.READY
            item = WorkItem(
                id=key_to_id[task.key],
                project_id=project_id,
                milestone_id=milestone.id,
                title=task.title,
                description=task.description,
                dependency_ids=dependency_ids,
                expected_outputs=task.expected_outputs,
                acceptance_criteria=self._with_expected_output_criteria(
                    task.expected_outputs, task.acceptance_criteria
                ),
                allowed_tools=["read_file", "write_file", "run_command"],
                authorized_files=[f"workspaces/{project_id}/**"],
                max_attempts=3,
                risk=task.risk,
                priority=task.priority,
                status=WorkItemStatus.DRAFT,
                owned_paths=task.owned_paths,
                shared_component=task.shared_component,
                output_strategy=task.output_strategy,
            )
            self.repository.add_work_item(item)
            self._transition(item, status, correlation_id)

        self.repository.add_decision(
            Decision(
                project_id=project_id,
                title="Alcance inicial conservador",
                decision=(
                    f"Ejecutar un único hito vertical con {len(plan.tasks)} "
                    "entregables dependientes."
                ),
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
            f"Brief y DAG creados con {len(plan.tasks)} tareas.",
            previous=ProjectStatus.PLANNING.value,
            new=final_status.value,
            correlation_id=correlation_id,
        )
        return project

    async def run_project(self, project_id: str) -> Project:
        project = self.repository.get_project(project_id)
        recovered = self.repository.recover_interrupted(project_id)
        if recovered:
            self._event(
                project_id,
                "execution_recovered",
                f"Se recuperaron {recovered} tareas interrumpidas de este proyecto.",
                correlation_id=new_id(),
            )
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

            if items and all(
                item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED}
                for item in items
            ):
                missing_deliverables, missing_scope, missing_success_criteria = (
                    self._project_contract_gaps(current_project, items)
                )
                if missing_deliverables or missing_scope or missing_success_criteria:
                    result = self.repository.update_project_status(
                        project_id,
                        ProjectStatus.FAILED,
                    )
                    self._event(
                        project_id,
                        "project_contract_failed",
                        (
                            "Las tareas terminaron, pero el DAG no cubre el contrato "
                            "completo del brief."
                        ),
                        previous=ProjectStatus.RUNNING.value,
                        new=ProjectStatus.FAILED.value,
                        metadata={
                            "missing_deliverables": missing_deliverables,
                            "missing_scope": missing_scope,
                            "missing_success_criteria": missing_success_criteria,
                        },
                        correlation_id=correlation_id,
                    )
                    return result
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

        session: WorktreeSession | None = None
        try:
            work_proposal = self._validate_work(work_response.content)
            prior_artifacts = [
                artifact
                for artifact in self.repository.list_artifacts(item.project_id)
                if artifact.work_item_id == item.id
            ]
            if (
                item.attempt_count > 1
                and prior_artifacts
                and self._candidate_matches_artifact(
                    work_proposal,
                    prior_artifacts[-1],
                )
            ):
                raise InvalidPlan(
                    "Retry candidate files are byte-for-byte identical to the "
                    "previous rejected candidate"
                )
            colliding_paths = self._colliding_dependency_paths(item, work_proposal)
            if colliding_paths:
                raise InvalidPlan(
                    "Workspace file paths collide with files already owned by "
                    "an unrelated task: " + ", ".join(sorted(colliding_paths))
                )
            if not self._work_declaration_matches_item(work_proposal, item):
                self._event(
                    item.project_id,
                    "artifact_criterion_declaration_mismatch",
                    (
                        "La declaración de criterios no coincide con la tarea; "
                        "el contenido continuará a evaluación semántica."
                    ),
                    work_item_id=item.id,
                    attempt=item.attempt_count,
                    correlation_id=correlation_id,
                )
            session = await self.isolation.prepare(
                item.project_id,
                item.id,
                item.attempt_count,
            )
            workspace_evidence = self.workspace.stage(
                item.project_id,
                session.path,
                work_proposal.files,
            )
            changes = await self.isolation.collect(session)
        except (InvalidPlan, WorkspaceRejected, IsolationError) as exc:
            # The rejected worktree is released here, before any split can
            # create children: a subtask must never inherit an attempt's
            # abandoned worktree. attempt_count was already incremented once
            # at the top of this method and is not touched again on this path.
            if session is not None:
                await self.isolation.discard(session)
            may_retry, may_split = self._candidate_failure_policy(exc)
            self._event(
                item.project_id,
                "workspace_action_rejected",
                f"La acción de workspace fue rechazada: {exc}",
                work_item_id=item.id,
                attempt=item.attempt_count,
                error=str(exc),
                metadata={"may_retry": may_retry, "may_split": may_split},
                correlation_id=correlation_id,
            )
            if may_retry and item.attempt_count < item.max_attempts:
                self._transition(
                    item,
                    WorkItemStatus.READY,
                    correlation_id,
                    error=str(exc),
                )
            elif may_split:
                await self._on_attempts_exhausted(item, correlation_id, str(exc))
            else:
                self._transition(
                    item,
                    WorkItemStatus.FAILED,
                    correlation_id,
                    error=str(exc),
                )
            return

        assert session is not None
        try:
            await self._evaluate_candidate(
                item,
                correlation_id,
                worker_run.id,
                work_proposal,
                workspace_evidence,
                changes,
            )
        finally:
            await self.isolation.discard(session)

    async def re_evaluate_artifact(
        self,
        work_item_id: str,
        artifact_id: str,
    ) -> WorkItem:
        item = self.repository.get_work_item(work_item_id)
        if item.status is not WorkItemStatus.READY:
            raise ValueError("Recovered artifact evaluation requires a ready task")
        source = next(
            (
                artifact
                for artifact in self.repository.list_artifacts(item.project_id)
                if artifact.id == artifact_id
            ),
            None,
        )
        if source is None:
            raise ValueError("Artifact does not exist in the task project")
        approved_dependency_artifact_ids = {
            review.artifact_id
            for review in self.repository.list_reviews(item.project_id)
            if review.work_item_id in item.dependency_ids
            and review.verdict is ReviewVerdict.APPROVED
        }
        work_proposal = self._recovery_work_proposal(
            source,
            item,
            approved_dependency_artifact_ids=approved_dependency_artifact_ids,
        )
        correlation_id = new_id()
        item = self._transition(item, WorkItemStatus.ASSIGNED, correlation_id)
        item = self._transition(item, WorkItemStatus.RUNNING, correlation_id)
        item = self.repository.increment_attempt(item.id)
        session: WorktreeSession | None = None
        try:
            session = await self.isolation.prepare(
                item.project_id,
                item.id,
                item.attempt_count,
            )
            workspace_evidence = self.workspace.stage(
                item.project_id,
                session.path,
                work_proposal.files,
            )
            changes = await self.isolation.collect(session)
            self._event(
                item.project_id,
                "artifact_candidate_recovered",
                "Un candidato anterior se rematerializÃ³ para reevaluaciÃ³n completa.",
                work_item_id=item.id,
                agent_run_id=source.agent_run_id,
                attempt=item.attempt_count,
                metadata={"source_artifact_id": source.id},
                correlation_id=correlation_id,
            )
            await self._evaluate_candidate(
                item,
                correlation_id,
                source.agent_run_id,
                work_proposal,
                workspace_evidence,
                changes,
            )
        except (InvalidPlan, WorkspaceRejected, IsolationError) as exc:
            current = self.repository.get_work_item(item.id)
            target = (
                WorkItemStatus.READY
                if current.attempt_count < current.max_attempts
                else WorkItemStatus.FAILED
            )
            self._transition(
                current,
                target,
                correlation_id,
                error=str(exc),
            )
        finally:
            if session is not None:
                await self.isolation.discard(session)
        return self.repository.get_work_item(item.id)

    async def evaluate_operator_candidate(
        self,
        work_item_id: str,
        work_proposal: WorkArtifactProposal,
    ) -> WorkItem:
        item = self.repository.get_work_item(work_item_id)
        if item.status is not WorkItemStatus.READY:
            raise ValueError("Operator candidate evaluation requires a ready task")
        correlation_id = new_id()
        run = AgentRun(
            project_id=item.project_id,
            work_item_id=item.id,
            agent_role=AgentRole.IMPLEMENTATION_WORKER,
            model="operator-candidate",
            provider="local",
            attempt=item.attempt_count + 1,
            outcome=RunOutcome.ARTIFACT_DELIVERED,
            input_summary="Candidato presentado por un operador local.",
            output_summary=work_proposal.summary,
            resource_usage=ResourceUsage(
                model="operator-candidate",
                provider="local",
            ),
            correlation_id=correlation_id,
        )
        self.repository.add_agent_run(run)
        item = self._transition(item, WorkItemStatus.ASSIGNED, correlation_id)
        item = self._transition(item, WorkItemStatus.RUNNING, correlation_id)
        item = self.repository.increment_attempt(item.id)
        session: WorktreeSession | None = None
        try:
            session = await self.isolation.prepare(
                item.project_id,
                item.id,
                item.attempt_count,
            )
            workspace_evidence = self.workspace.stage(
                item.project_id,
                session.path,
                work_proposal.files,
            )
            changes = await self.isolation.collect(session)
            self._event(
                item.project_id,
                "operator_candidate_submitted",
                "Un operador local presentÃ³ un candidato para evaluaciÃ³n completa.",
                work_item_id=item.id,
                agent_run_id=run.id,
                attempt=item.attempt_count,
                correlation_id=correlation_id,
            )
            await self._evaluate_candidate(
                item,
                correlation_id,
                run.id,
                work_proposal,
                workspace_evidence,
                changes,
            )
        except (InvalidPlan, WorkspaceRejected, IsolationError) as exc:
            current = self.repository.get_work_item(item.id)
            target = (
                WorkItemStatus.READY
                if current.attempt_count < current.max_attempts
                else WorkItemStatus.FAILED
            )
            self._transition(
                current,
                target,
                correlation_id,
                error=str(exc),
            )
        finally:
            if session is not None:
                await self.isolation.discard(session)
        return self.repository.get_work_item(item.id)

    async def _evaluate_candidate(
        self,
        item: WorkItem,
        correlation_id: str,
        worker_run_id: str,
        work_proposal: WorkArtifactProposal,
        workspace_evidence: list[WorkspaceFileEvidence],
        changes: GitChangeSet,
    ) -> None:
        artifact_content = work_proposal.model_dump(mode="json")
        artifact_content["isolation"] = {
            "backend": "git_worktree",
            "branch": changes.session.branch,
            "commit": changes.commit,
            "files": list(changes.files),
        }
        path, checksum = self.artifacts.materialize(
            item.project_id,
            item.id,
            item.attempt_count,
            artifact_content,
        )
        artifact = Artifact(
            project_id=item.project_id,
            work_item_id=item.id,
            agent_run_id=worker_run_id,
            artifact_type=work_proposal.artifact_type,
            title=work_proposal.title,
            content=artifact_content,
            file_paths=[path],
            checksum=checksum,
        )
        self.repository.add_artifact(artifact)
        self._event(
            item.project_id,
            "workspace_files_materialized",
            (
                f"Se prepararon {len(workspace_evidence)} archivos en un "
                "worktree aislado."
            ),
            work_item_id=item.id,
            agent_run_id=worker_run_id,
            attempt=item.attempt_count,
            metadata={
                "branch": changes.session.branch,
                "commit": changes.commit,
                "files": [evidence.path for evidence in workspace_evidence],
            },
            correlation_id=correlation_id,
        )
        item = self._transition(
            self.repository.get_work_item(item.id),
            WorkItemStatus.AWAITING_REVIEW,
            correlation_id,
        )

        # Reading the files back can fail for reasons that have nothing to do
        # with the candidate (full disk, revoked permission, I/O error). This
        # runs after the task already moved to AWAITING_REVIEW, outside the
        # rejection routing of _execute_work_item, so an escaping error here
        # would take the whole request down. Treat it as an unverified check
        # and let the technical gate decide, with the cause on the record.
        try:
            workspace_checks = [
                evidence.as_check(verified=self.workspace.verify(evidence))
                for evidence in workspace_evidence
            ]
            control_verified = self.artifacts.verify(path, checksum)
        except (WorkspaceInfrastructureRejected, OSError) as exc:
            self._event(
                item.project_id,
                "workspace_verification_unavailable",
                (
                    "No se pudieron releer los archivos para verificar su "
                    f"checksum: {exc}"
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                error=str(exc),
                correlation_id=correlation_id,
            )
            workspace_checks = [
                evidence.as_check(verified=False) for evidence in workspace_evidence
            ]
            control_verified = False
        try:
            validation_results = await self.validations.validate(
                item.project_id,
                workspace_evidence,
                validation_root=changes.session.path,
                acceptance_criteria=item.acceptance_criteria,
                expected_outputs=item.expected_outputs,
                execution_contract=item.execution_contract,
            )
            validation_checks = [result.as_evidence() for result in validation_results]
        except CommandRejected as exc:
            validation_checks = [
                {
                    "check": "validation_profile",
                    "profile": "policy",
                    "targets": [],
                    "command": [],
                    "cwd": "",
                    "stdout": "",
                    "stderr": str(exc),
                    "return_code": -1,
                    "timed_out": False,
                    "passed": False,
                }
            ]
        profiles_passed = all(bool(check["passed"]) for check in validation_checks)
        isolation_check = changes.as_evidence()
        file_verified = (
            control_verified
            and all(bool(check["verified"]) for check in workspace_checks)
            and profiles_passed
            and bool(isolation_check["verified"])
        )
        self._event(
            item.project_id,
            "validation_profiles_completed",
            (
                f"{len(validation_checks)} perfiles de validación completados."
                if profiles_passed
                else "Al menos un perfil de validación falló."
            ),
            work_item_id=item.id,
            attempt=item.attempt_count,
            error=None if profiles_passed else "Validation profile failed",
            metadata={
                "profiles": [check["profile"] for check in validation_checks],
                "passed": profiles_passed,
            },
            correlation_id=correlation_id,
        )
        report_passed = self._technical_evidence_passed(
            file_verified=file_verified,
            profiles_passed=profiles_passed,
        )
        try:
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
                        "workspace_files": workspace_checks,
                        "validation_profiles": validation_checks,
                        "isolation": isolation_check,
                        "acceptance_criteria": item.acceptance_criteria,
                    },
                ),
                correlation_id,
            )
        except RoleExecutionError as exc:
            await self._reject_evaluation(item, correlation_id, str(exc))
            return
        try:
            test_proposal = self._validate_test(test_response.content)
        except InvalidPlan as exc:
            test_proposal = self._fallback_test_evaluation(
                report_passed=report_passed,
                reason=str(exc),
            )
            self._event(
                item.project_id,
                "tester_explanation_normalized",
                (
                    "La explicación del tester no cumplió el contrato; "
                    "se conservó el resultado de la compuerta técnica fija."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                error=str(exc),
                correlation_id=correlation_id,
            )
        report = TestReport(
            project_id=item.project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            tester_run_id=tester_run.id,
            passed=report_passed,
            checks=[
                {
                    "name": "fixed_evidence_gate",
                    "passed": report_passed,
                    "evidence": (
                        "Checksums, aislamiento y perfiles locales son la fuente de verdad."
                    ),
                },
                {
                    "name": "model_assessment",
                    "passed": test_proposal.passed,
                    "evidence": "Evaluación explicativa del tester local.",
                },
                *[
                    check.model_dump(mode="json")
                    for check in test_proposal.checks
                ],
            ],
            command_evidence=[
                {
                    "check": "materialized_file_checksum",
                    "path": path,
                    "verified": control_verified,
                },
                *workspace_checks,
                *validation_checks,
                isolation_check,
            ],
            summary=test_proposal.summary,
        )
        self.repository.add_test_report(report)

        dependency_artifacts = self.memory.dependency_artifacts(item)
        effective_criteria = (
            [*item.acceptance_criteria, DEPENDENCY_CONSISTENCY_CRITERION]
            if dependency_artifacts
            else list(item.acceptance_criteria)
        )
        semantic_review_criteria = self._semantic_review_criteria(
            effective_criteria,
            report_passed=report.passed,
        )
        try:
            review_response, reviewer_run = await self._call_role(
                AgentRole.CRITICAL_REVIEWER,
                ModelRequest(
                    operation="review",
                    project_id=item.project_id,
                    work_item_id=item.id,
                    attempt=item.attempt_count,
                    payload=self._review_payload(
                        artifact.content,
                        [
                            file.model_dump(mode="json")
                            for file in work_proposal.files
                        ],
                        semantic_review_criteria,
                        dependency_artifacts,
                    ),
                ),
                correlation_id,
            )
        except RoleExecutionError as exc:
            await self._reject_evaluation(item, correlation_id, str(exc))
            return
        try:
            review_fragments, initial_review_error = (
                self._recover_initial_review_fragment(
                    review_response.content,
                    semantic_review_criteria,
                )
            )
            covered_criteria = {
                criterion
                for fragment in review_fragments
                for criterion in fragment.acceptance_results
            }
            missing_criteria = [
                criterion
                for criterion in semantic_review_criteria
                if criterion not in covered_criteria
            ]
            for criterion in missing_criteria:
                fragment_response, _ = await self._call_role(
                    AgentRole.CRITICAL_REVIEWER,
                    ModelRequest(
                        operation="review",
                        project_id=item.project_id,
                        work_item_id=item.id,
                        attempt=item.attempt_count,
                        payload=self._review_payload(
                            artifact.content,
                            [
                                file.model_dump(mode="json")
                                for file in work_proposal.files
                            ],
                            [criterion],
                            dependency_artifacts,
                        ),
                    ),
                    correlation_id,
                )
                try:
                    fragment = self._validate_focused_review(
                        fragment_response.content,
                        criterion,
                    )
                except InvalidPlan:
                    fragment = ReviewEvaluationProposal(
                        verdict=ReviewVerdict.CHANGES_REQUESTED.value,
                        reasons=[
                            (
                                "El revisor local no aportó una respuesta focalizada "
                                f"válida para: {criterion}"
                            )
                        ],
                        acceptance_results={criterion: False},
                    )
                review_fragments.append(
                    fragment
                )
            if not report.passed:
                covered_criteria = {
                    criterion
                    for fragment in review_fragments
                    for criterion in fragment.acceptance_results
                }
                for criterion in effective_criteria:
                    if criterion in covered_criteria:
                        continue
                    review_fragments.append(
                        ReviewEvaluationProposal(
                            verdict=ReviewVerdict.CHANGES_REQUESTED.value,
                            reasons=[
                                (
                                    "La compuerta técnica ya rechazó la entrega; "
                                    "el criterio queda pendiente del próximo intento."
                                )
                            ],
                            acceptance_results={criterion: False},
                        )
                    )
            review_proposal = self._merge_review_fragments(
                review_fragments,
                effective_criteria,
            )
        except (InvalidPlan, RoleExecutionError) as exc:
            await self._reject_evaluation(item, correlation_id, str(exc))
            return
        if initial_review_error is not None:
            self._event(
                item.project_id,
                "review_initial_response_recovered",
                (
                    "La respuesta inicial del revisor no cumpliÃ³ el contrato; "
                    "se reevaluaron individualmente todos los criterios."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                error=initial_review_error,
                metadata={"criteria": missing_criteria},
                correlation_id=correlation_id,
            )
        if missing_criteria:
            self._event(
                item.project_id,
                "review_coverage_recovered",
                (
                    f"Se recuperaron {len(missing_criteria)} criterios omitidos "
                    "mediante revisiones focalizadas."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={"criteria": missing_criteria},
                correlation_id=correlation_id,
            )
        review_proposal = self._apply_technical_review_gate(
            review_proposal,
            effective_criteria,
            report_passed=report.passed,
            validation_checks=validation_checks,
        )
        raw_verdict = review_response.content.get("verdict")
        if raw_verdict != review_proposal.verdict:
            self._event(
                item.project_id,
                "review_verdict_normalized",
                (
                    "El veredicto del revisor se ajustó para coincidir con sus "
                    "resultados de aceptación."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={
                    "raw_verdict": raw_verdict,
                    "normalized_verdict": review_proposal.verdict,
                },
                correlation_id=correlation_id,
            )
        review = Review(
            project_id=item.project_id,
            work_item_id=item.id,
            artifact_id=artifact.id,
            reviewer_run_id=reviewer_run.id,
            verdict=ReviewVerdict(review_proposal.verdict),
            reasons=review_proposal.reasons,
            acceptance_results=review_proposal.acceptance_results,
        )
        self.repository.add_review(review)

        if report.passed and review.verdict is ReviewVerdict.APPROVED:
            try:
                integration = await self.isolation.integrate(changes)
                integrated_evidence = self.workspace.project_evidence(
                    item.project_id,
                    work_proposal.files,
                )
            except (IsolationError, WorkspaceRejected) as exc:
                self._event(
                    item.project_id,
                    "change_integration_failed",
                    f"No se pudo integrar el cambio aislado: {exc}",
                    work_item_id=item.id,
                    attempt=item.attempt_count,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
                await self._request_changes(
                    item,
                    correlation_id,
                    f"Falló la integración aislada: {exc}",
                )
                return
            self.repository.update_artifact_file_paths(
                artifact.id,
                [path, *(evidence.path for evidence in integrated_evidence)],
            )
            self._event(
                item.project_id,
                "change_set_integrated",
                f"Cambio aislado integrado en main ({integration.commit[:12]}).",
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={
                    "branch": changes.session.branch,
                    "candidate_commit": changes.commit,
                    "integration_commit": integration.commit,
                    "files": list(integration.files),
                },
                correlation_id=correlation_id,
            )
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
        await self._request_changes(
            item,
            correlation_id,
            "; ".join(review.reasons),
        )

    def _fail_planning(
        self,
        project_id: str,
        correlation_id: str,
        reason: str,
    ) -> Project:
        result = self.repository.update_project_status(project_id, ProjectStatus.FAILED)
        self._event(
            project_id,
            "planning_failed",
            f"La planificación no pudo completarse: {reason}",
            previous=ProjectStatus.PLANNING.value,
            new=ProjectStatus.FAILED.value,
            error=reason,
            correlation_id=correlation_id,
        )
        return result

    async def _reject_evaluation(
        self,
        item: WorkItem,
        correlation_id: str,
        reason: str,
    ) -> None:
        self._event(
            item.project_id,
            "evaluation_response_rejected",
            f"La evaluación del modelo fue rechazada: {reason}",
            work_item_id=item.id,
            attempt=item.attempt_count,
            error=reason,
            correlation_id=correlation_id,
        )
        await self._request_changes(item, correlation_id, reason)

    async def _request_changes(
        self,
        item: WorkItem,
        correlation_id: str,
        reason: str,
    ) -> None:
        item = self._transition(
            item,
            WorkItemStatus.CHANGES_REQUESTED,
            correlation_id,
            error=reason,
        )
        if item.attempt_count < item.max_attempts:
            self._transition(
                item,
                WorkItemStatus.READY,
                correlation_id,
                error=reason,
            )
        else:
            await self._on_attempts_exhausted(item, correlation_id, reason)

    @staticmethod
    def _candidate_failure_policy(exc: Exception) -> tuple[bool, bool]:
        """`(may_retry, may_split)` for a rejected candidate.

        Splitting a task is a *planning* answer: it only makes sense when the
        proposed content was wrong in a way a smaller scope could fix. A
        boundary violation and a broken disk are not planning problems, and
        letting either reach `_attempt_split` would spawn subtasks in response
        to something no subtask can fix.
        """
        if isinstance(exc, WorkspaceSecurityRejected):
            # Escaping path or symlink: retrying repeats the violation and
            # decomposing it invents work that was never the problem.
            return False, False
        if isinstance(exc, (WorkspaceInfrastructureRejected, IsolationError)):
            # The machine failed, not the proposal. A retry can genuinely
            # succeed, but the task itself was never too broad.
            return True, False
        # InvalidPlan (malformed artifact, unsafe path in the proposal,
        # repeated candidate, colliding paths) and the plain content-limit
        # WorkspaceRejected: the model can plausibly do better, and once it
        # has stopped doing better, a narrower scope is the next lever.
        return True, True

    async def _on_attempts_exhausted(
        self,
        item: WorkItem,
        correlation_id: str,
        reason: str,
    ) -> None:
        if await self._attempt_split(item, correlation_id, reason):
            return
        self._transition(
            item,
            WorkItemStatus.FAILED,
            correlation_id,
            error=reason,
        )

    async def _attempt_split(
        self,
        item: WorkItem,
        correlation_id: str,
        reason: str,
    ) -> bool:
        if len(item.acceptance_criteria) < 2:
            return False
        if item.split_depth >= self.MAX_SPLIT_DEPTH:
            # One automatic split per lineage. Anything a split produced —
            # subtask or consolidation alike — fails instead of splitting
            # again, so an exhausted lineage stops and waits for a human
            # instead of breeding generations of ever-smaller tasks.
            # Deliberately reads the persisted depth, never the title: the
            # title is model-authored text, not a type.
            self._event(
                item.project_id,
                "task_split_depth_exhausted",
                (
                    "La tarea ya proviene de una división automática; no se "
                    "vuelve a dividir. Queda fallida para reparación manual."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={"split_depth": item.split_depth},
                correlation_id=correlation_id,
            )
            return False

        retry_guidance = self.memory.operational(item).get("retry_guidance", {})
        try:
            decompose_response, _ = await self._call_role(
                AgentRole.TECHNICAL_MANAGER,
                ModelRequest(
                    operation="decompose",
                    project_id=item.project_id,
                    work_item_id=item.id,
                    attempt=item.attempt_count,
                    payload={
                        "task": item.model_dump(mode="json"),
                        "acceptance_criteria_index": acceptance_criteria_index(
                            item.acceptance_criteria
                        ),
                        "prior_review_feedback": retry_guidance.get(
                            "prior_review_feedback", []
                        ),
                        "prior_validation_failures": retry_guidance.get(
                            "prior_validation_failures", []
                        ),
                    },
                ),
                correlation_id,
            )
        except RoleExecutionError:
            return False

        # Give this split its own sharing group: if the parent already had
        # one, its children extend it; otherwise a fresh one scoped to this
        # split keeps its own 2-4 children from colliding with each other,
        # independent of whatever any other split of a sibling task does.
        shared_component = item.shared_component or f"split-{item.id}"

        try:
            decompose_proposal = DecomposeProposal.model_validate(
                self._normalized_decompose_content(
                    decompose_response.content, shared_component, item
                )
            )
        except ValidationError:
            return False

        # Criteria come from the parent by id, never from the model's rewritten
        # text: a small model restates them almost every time, and comparing
        # that text is what used to abandon otherwise usable splits. If the
        # mapping is unusable, a deterministic partition still covers every
        # criterion exactly once, so coverage holds by construction.
        assignment = self._resolve_criteria_assignment(
            item.acceptance_criteria,
            decompose_proposal.subtasks,
        )
        if assignment is None:
            assignment = self._deterministic_criteria_assignment(
                len(item.acceptance_criteria),
                len(decompose_proposal.subtasks),
            )
            self._event(
                item.project_id,
                "task_split_criteria_partitioned",
                (
                    "El mapeo de criterios devuelto por el modelo no era usable; "
                    "se repartieron los criterios del padre de forma "
                    "determinista entre las subtareas."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={"criteria": len(item.acceptance_criteria)},
                correlation_id=correlation_id,
            )

        # Subtasks that claim the same file cannot run in parallel: nothing
        # merges content at integration time (WorkspaceMaterializer.stage
        # replaces the file), and the shared_component exemption in
        # _colliding_dependency_paths would let the second one overwrite the
        # first silently. Chain exactly those, leave the rest parallel.
        # A deterministic partition can use fewer subtasks than the model
        # proposed (never more criteria than there are to hand out), so the
        # assignment decides how many children there are.
        subtasks = decompose_proposal.subtasks[: len(assignment)]
        groups = self._group_overlapping_claims(
            [
                {path.casefold() for path in subtask.claimed_paths()}
                for subtask in subtasks
            ]
        )

        children: list[WorkItem] = []
        chain_tails: list[WorkItem] = []
        chains: list[list[str]] = []
        for group in groups:
            previous: WorkItem | None = None
            chain: list[str] = []
            for index in group:
                subtask = subtasks[index]
                child = WorkItem(
                    project_id=item.project_id,
                    milestone_id=item.milestone_id,
                    title=f"[subtarea] {subtask.title}",
                    description=subtask.description,
                    # The parent's own dependencies stay on every child (they
                    # are COMPLETED by construction); a chained child adds the
                    # previous link so it sees the content it must extend.
                    dependency_ids=(
                        list(item.dependency_ids)
                        if previous is None
                        else [*item.dependency_ids, previous.id]
                    ),
                    expected_outputs=subtask.expected_outputs,
                    # The inherited slice is always the parent's own wording,
                    # never the model's — plus a fresh, deterministic criterion
                    # for whatever new expected_outputs this subtask declares
                    # on its own (P1.3b). Merging is idempotent, so this never
                    # duplicates anything already present in the slice.
                    acceptance_criteria=self._with_expected_output_criteria(
                        subtask.expected_outputs,
                        [
                            item.acceptance_criteria[position]
                            for position in assignment[index]
                        ],
                    ),
                    allowed_tools=list(item.allowed_tools),
                    authorized_files=list(item.authorized_files),
                    max_attempts=3,
                    risk=item.risk,
                    requires_approval=item.requires_approval,
                    priority=item.priority,
                    status=(
                        WorkItemStatus.READY
                        if previous is None
                        else WorkItemStatus.BLOCKED
                    ),
                    owned_paths=list(subtask.owned_paths),
                    shared_component=shared_component,
                    output_strategy=(
                        OutputStrategy.FRAGMENT
                        if previous is None
                        else OutputStrategy.PATCH
                    ),
                    split_depth=item.split_depth + 1,
                    # Deliberately not inherited (ADR 0027): the parent's
                    # contract names a specific entrypoint file that a split
                    # child may not even own post-split.
                )
                self.repository.add_work_item(child)
                children.append(child)
                chain.append(child.id)
                previous = child
            # A group always has at least one member, so the last child added
            # is this chain's tail (an ungrouped subtask is its own tail).
            chain_tails.append(children[-1])
            chains.append(chain)

        consolidation_owned_paths = list(item.owned_paths) or sorted(
            {path for child in children for path in child.owned_paths}
        )
        consolidation = WorkItem(
            project_id=item.project_id,
            milestone_id=item.milestone_id,
            title=f"Consolidar subtareas: {item.title}",
            description=(
                "Confirmar que las subtareas cubren completamente el "
                f"contrato original: {item.description}"
            ),
            # The tail of each chain transitively covers the links before it,
            # so depending on tails plus ungrouped subtasks waits for all of
            # them without duplicating edges.
            dependency_ids=[tail.id for tail in chain_tails],
            expected_outputs=list(item.expected_outputs),
            # Defensively re-derived (not just copied): idempotent merging
            # means this is a no-op when the parent's criteria already cover
            # its own expected_outputs, without relying on that always being
            # true elsewhere (P1.3b).
            acceptance_criteria=self._with_expected_output_criteria(
                item.expected_outputs, item.acceptance_criteria
            ),
            allowed_tools=list(item.allowed_tools),
            authorized_files=list(item.authorized_files),
            max_attempts=3,
            risk=item.risk,
            requires_approval=item.requires_approval,
            priority=item.priority,
            status=WorkItemStatus.BLOCKED,
            owned_paths=consolidation_owned_paths,
            shared_component=shared_component,
            output_strategy=OutputStrategy.CONSOLIDATION,
            # Same lineage as the children it consolidates: an exhausted
            # consolidation is terminal too, not a new decomposition round.
            split_depth=item.split_depth + 1,
            # Same reasoning as the children: not inherited (ADR 0027).
        )
        self.repository.add_work_item(consolidation)

        self.repository.retarget_dependency(item.project_id, item.id, consolidation.id)

        self._transition(
            item,
            WorkItemStatus.CANCELLED,
            correlation_id,
            error=(
                f"Dividida en {len(children)} subtareas tras agotar intentos: "
                f"{reason}"
            ),
        )
        self._event(
            item.project_id,
            "task_split_created",
            (
                f"{item.title} se dividió en {len(children)} subtareas tras "
                "agotar intentos sin producir una entrega aceptable."
            ),
            work_item_id=item.id,
            attempt=item.attempt_count,
            metadata={
                "child_ids": [child.id for child in children],
                "consolidation_id": consolidation.id,
                "chains": chains,
                "reason": reason,
            },
            correlation_id=correlation_id,
        )
        chained = [chain for chain in chains if len(chain) > 1]
        if chained:
            self._event(
                item.project_id,
                "task_split_chained_overlapping_paths",
                (
                    f"{len(chained)} grupo(s) de subtareas reclamaron el mismo "
                    "archivo; se encadenaron en secuencia con estrategia "
                    "'patch' en vez de ejecutarse en paralelo."
                ),
                work_item_id=item.id,
                attempt=item.attempt_count,
                metadata={
                    "chains": chained,
                    "claims": {
                        child.id: child.owned_paths
                        for child in children
                        if any(child.id in chain for chain in chained)
                    },
                },
                correlation_id=correlation_id,
            )
        return True

    @staticmethod
    def _normalized_decompose_content(
        content: dict[str, Any],
        shared_component: str,
        item: WorkItem,
    ) -> dict[str, Any]:
        """Fill in the contract the model keeps leaving unset.

        `_attempt_split` regroups every subtask under one `shared_component`
        anyway, so validating the raw response would only turn the model's
        default `exclusive` into a hard failure of an otherwise usable split.
        Normalizing first also promotes the file-like `expected_outputs` the
        model does use into real `owned_paths`, and falls back to the parent's
        own outputs and claims when the model leaves them out entirely — the
        contract still demands at least one output, it just no longer has to
        come from the model. Children that end up inheriting the same file are
        chained, not run in parallel, by the grouping further down.
        """
        subtasks = content.get("subtasks")
        if not isinstance(subtasks, list):
            return content
        inherited_outputs = list(item.expected_outputs)
        inherited_claims = merge_path_claims(
            list(item.owned_paths),
            list(item.expected_outputs),
        )
        normalized: list[Any] = []
        for subtask in subtasks:
            if not isinstance(subtask, dict):
                return content
            owned = subtask.get("owned_paths") or []
            outputs = subtask.get("expected_outputs") or []
            if not isinstance(owned, list) or not isinstance(outputs, list):
                return content
            owned = [str(value) for value in owned]
            outputs = [str(value) for value in outputs if str(value).strip()]
            if outputs:
                claims = merge_path_claims(owned, outputs)
            else:
                # Only when the subtask declared no output at all. A declared
                # but non-file output ("un informe en prosa") is a real answer
                # and must not silently inherit the parent's file, which would
                # chain subtasks that never touch the same path.
                outputs = inherited_outputs
                claims = merge_path_claims(owned, outputs) or inherited_claims
            normalized.append(
                {
                    **subtask,
                    "expected_outputs": outputs,
                    "owned_paths": claims[:20],
                    "shared_component": shared_component,
                    "output_strategy": OutputStrategy.FRAGMENT.value,
                }
            )
        return {**content, "subtasks": normalized}

    @staticmethod
    def _resolve_criteria_assignment(
        criteria: list[str],
        subtasks: list[SubtaskProposal],
    ) -> list[list[int]] | None:
        """Parent criterion positions per subtask, or `None` if unusable.

        Unusable means anything that would silently drop or duplicate work:
        an id that names no parent criterion, the same id handed to two
        subtasks, a subtask left with nothing to do, or a parent criterion
        nobody picked up.
        """
        by_id = {
            entry["id"]: position
            for position, entry in enumerate(acceptance_criteria_index(criteria))
        }
        assignment: list[list[int]] = []
        claimed: set[int] = set()
        for subtask in subtasks:
            positions: list[int] = []
            for criterion_id in subtask.acceptance_criteria_ids:
                position = by_id.get(criterion_id)
                if position is None or position in claimed:
                    return None  # unknown or already handed to another subtask
                claimed.add(position)
                positions.append(position)
            if not positions:
                return None  # a subtask with no criterion has nothing to verify
            assignment.append(positions)
        if len(claimed) != len(criteria):
            return None  # something the parent had to satisfy went missing
        return assignment

    @staticmethod
    def _deterministic_criteria_assignment(
        criteria_count: int,
        subtask_count: int,
    ) -> list[list[int]]:
        """Contiguous, balanced partition of every parent criterion.

        The fallback for when the model's mapping is unusable. Never leaves a
        child empty, so the number of children drops to the number of criteria
        when the model proposed more subtasks than there is work to split.
        """
        buckets = max(2, min(subtask_count, criteria_count))
        base, remainder = divmod(criteria_count, buckets)
        assignment: list[list[int]] = []
        position = 0
        for bucket in range(buckets):
            size = base + (1 if bucket < remainder else 0)
            assignment.append(list(range(position, position + size)))
            position += size
        return assignment

    @staticmethod
    def _group_overlapping_claims(claims: list[set[str]]) -> list[list[int]]:
        """Connected components of subtasks that claim a path in common.

        Transitive on purpose: if A and B share `api.py` and B and C share
        `models.py`, all three end up in one chain — running B twice in two
        different chains would reintroduce the overwrite this prevents.
        """
        parent = list(range(len(claims)))

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for index, claim in enumerate(claims):
            if not claim:
                continue
            for other in range(index + 1, len(claims)):
                if not claim & claims[other]:
                    continue
                root, other_root = find(index), find(other)
                if root != other_root:
                    parent[max(root, other_root)] = min(root, other_root)

        groups: dict[int, list[int]] = {}
        for index in range(len(claims)):
            groups.setdefault(find(index), []).append(index)
        return [groups[key] for key in sorted(groups)]

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
        # attempt_count is untouched by a status transition, so item's own
        # value is still correct after the update — no need to re-fetch.
        event = ExecutionEvent(
            project_id=item.project_id,
            work_item_id=item.id,
            action="task_state_changed",
            message=f"{item.title}: {current.value} → {target.value}",
            previous_state=current.value,
            new_state=target.value,
            attempt=item.attempt_count,
            error=error,
            correlation_id=correlation_id,
        )
        return self.repository.transition_work_item(
            item.id,
            target,
            error=error,
            event=event,
        )

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
        metadata: dict[str, Any] | None = None,
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
                metadata=metadata or {},
            )
        )

    @staticmethod
    def _validate_plan(content: dict[str, Any]) -> PlanProposal:
        try:
            return PlanProposal.model_validate(content)
        except ValidationError as exc:
            raise InvalidPlan(f"Invalid project plan: {exc}") from exc

    @staticmethod
    def _ensure_plan_covers_brief(
        plan: PlanProposal,
        brief: ProjectBrief,
    ) -> tuple[PlanProposal, list[str], list[str], list[str]]:
        outputs = {
            Orchestrator._planning_entry_key(output)
            for task in plan.tasks
            for output in task.expected_outputs
        }
        criteria = {
            Orchestrator._planning_entry_key(criterion)
            for task in plan.tasks
            for criterion in task.acceptance_criteria
        }
        missing_deliverables = [
            deliverable
            for deliverable in brief.deliverables
            if Orchestrator._planning_entry_key(deliverable) not in outputs
        ]
        missing_scope = [
            scope_entry
            for scope_entry in brief.scope
            if Orchestrator._planning_entry_key(scope_entry) not in criteria
        ]
        missing_success_criteria = [
            criterion
            for criterion in brief.success_criteria
            if Orchestrator._planning_entry_key(criterion) not in criteria
        ]
        if (
            not missing_deliverables
            and not missing_scope
            and not missing_success_criteria
        ):
            return plan, [], [], []

        depended_on = {
            dependency
            for task in plan.tasks
            for dependency in task.dependencies
        }
        terminal_keys = [
            task.key
            for task in plan.tasks
            if task.key not in depended_on
        ]
        existing_keys = {task.key for task in plan.tasks}
        closing_key = "complete_project_delivery"
        suffix = 2
        while closing_key in existing_keys:
            closing_key = f"complete_project_delivery_{suffix}"
            suffix += 1

        closing_title = "Completar y verificar la entrega del proyecto"
        existing_titles = {
            task.title.strip().casefold()
            for task in plan.tasks
        }
        if closing_title.casefold() in existing_titles:
            closing_title = f"{closing_title} ({suffix - 1})"

        closing_task = TaskProposal(
            key=closing_key,
            title=closing_title,
            description=(
                "Integrar los resultados anteriores y materializar el contrato "
                f"completo del brief: {brief.summary}"
            ),
            dependencies=terminal_keys,
            expected_outputs=list(brief.deliverables),
            acceptance_criteria=Orchestrator._unique_planning_entries(
                [*brief.scope, *brief.success_criteria]
            ),
            risk=RiskLevel.MEDIUM,
            priority=70,
            output_strategy=OutputStrategy.CONSOLIDATION,
        )
        completed_plan = PlanProposal.model_validate(
            {
                "milestone": plan.milestone.model_dump(mode="json"),
                "tasks": [
                    task.model_dump(mode="json")
                    for task in [*plan.tasks, closing_task]
                ],
            }
        )
        return (
            completed_plan,
            missing_deliverables,
            missing_scope,
            missing_success_criteria,
        )

    @staticmethod
    def _plan_dependency_closure(plan: PlanProposal) -> dict[str, set[str]]:
        graph = {task.key: task.dependencies for task in plan.tasks}
        closures: dict[str, set[str]] = {}
        for key in graph:
            seen: set[str] = set()
            pending = list(graph[key])
            while pending:
                dependency_key = pending.pop()
                if dependency_key in seen:
                    continue
                seen.add(dependency_key)
                pending.extend(graph.get(dependency_key, []))
            closures[key] = seen
        return closures

    @staticmethod
    def _detect_owned_path_conflicts(plan: PlanProposal) -> list[dict[str, Any]]:
        closures = Orchestrator._plan_dependency_closure(plan)
        tasks = plan.tasks
        conflicts: list[dict[str, Any]] = []
        for index, task_a in enumerate(tasks):
            for task_b in tasks[index + 1 :]:
                if (
                    task_b.key in closures[task_a.key]
                    or task_a.key in closures[task_b.key]
                ):
                    continue  # real dependency edge, not siblings
                # Claims come from owned_paths *and* file-like expected_outputs:
                # models keep declaring the file they will write in the second
                # field and leave the first empty (ADR 0023 live evidence), so
                # reading only owned_paths made this preflight unreachable.
                by_key_a = {path.casefold(): path for path in task_a.claimed_paths()}
                by_key_b = {path.casefold(): path for path in task_b.claimed_paths()}
                overlap = set(by_key_a) & set(by_key_b)
                if not overlap:
                    continue
                grouped = (
                    task_a.shared_component is not None
                    and task_a.shared_component == task_b.shared_component
                    and task_a.output_strategy != OutputStrategy.EXCLUSIVE
                    and task_b.output_strategy != OutputStrategy.EXCLUSIVE
                )
                if grouped:
                    continue
                explicit = {path.casefold() for path in task_a.owned_paths} & {
                    path.casefold() for path in task_b.owned_paths
                }
                conflicts.append(
                    {
                        "task_a": task_a.key,
                        "task_b": task_b.key,
                        "paths": sorted(by_key_a[key] for key in overlap),
                        "declared_via": sorted(
                            {
                                "owned_paths" if key in explicit else "expected_outputs"
                                for key in overlap
                            }
                        ),
                    }
                )
        return conflicts

    async def _resolve_owned_path_conflicts(
        self,
        plan: PlanProposal,
        brief: ProjectBrief,
        project_id: str,
        correlation_id: str,
        *,
        max_revisions: int = 2,
    ) -> PlanProposal:
        current = plan
        for _ in range(max_revisions):
            conflicts = self._detect_owned_path_conflicts(current)
            if not conflicts:
                return current
            self._event(
                project_id,
                "plan_owned_path_conflict_detected",
                (
                    f"Se detectaron {len(conflicts)} conflictos de propiedad "
                    "de archivo en el plan; pidiendo una revisión."
                ),
                metadata={"conflicts": conflicts},
                correlation_id=correlation_id,
            )
            try:
                revision_response, _ = await self._call_role(
                    AgentRole.TECHNICAL_MANAGER,
                    ModelRequest(
                        operation="plan_revision",
                        project_id=project_id,
                        payload={
                            "brief": brief.model_dump(mode="json"),
                            "previous_plan": current.model_dump(mode="json"),
                            "path_conflicts": conflicts,
                            "runtime_capabilities": self.memory.capabilities.model_dump(
                                mode="json"
                            ),
                        },
                    ),
                    correlation_id,
                )
                revised = self._validate_plan(revision_response.content)
            except (RoleExecutionError, InvalidPlan):
                break
            revised, _, _, _ = self._ensure_plan_covers_brief(revised, brief)
            current = revised
        remaining = self._detect_owned_path_conflicts(current)
        if remaining:
            self._event(
                project_id,
                "plan_owned_path_conflict_unresolved",
                (
                    f"Quedaron {len(remaining)} conflictos de propiedad de "
                    "archivo sin resolver tras la revisión; la compuerta de "
                    "colisión en ejecución sigue siendo la protección final."
                ),
                metadata={"conflicts": remaining},
                correlation_id=correlation_id,
            )
        return current

    @staticmethod
    def _project_contract_gaps(
        project: Project,
        items: list[WorkItem],
    ) -> tuple[list[str], list[str], list[str]]:
        brief = project.brief
        if brief is None:
            return [], [], []
        outputs = {
            Orchestrator._planning_entry_key(output)
            for item in items
            for output in item.expected_outputs
        }
        criteria = {
            Orchestrator._planning_entry_key(criterion)
            for item in items
            for criterion in item.acceptance_criteria
        }
        return (
            [
                deliverable
                for deliverable in brief.deliverables
                if Orchestrator._planning_entry_key(deliverable) not in outputs
            ],
            [
                scope_entry
                for scope_entry in brief.scope
                if Orchestrator._planning_entry_key(scope_entry) not in criteria
            ],
            [
                criterion
                for criterion in brief.success_criteria
                if Orchestrator._planning_entry_key(criterion) not in criteria
            ],
        )

    @staticmethod
    def _planning_entry_key(value: str) -> str:
        return " ".join(value.split()).casefold()

    @staticmethod
    def _unique_planning_entries(values: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = Orchestrator._planning_entry_key(value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique

    @staticmethod
    def _expected_output_criterion(expected_output: str) -> str:
        return f"El entregable esperado existe y está completo: {expected_output}"

    @staticmethod
    def _with_expected_output_criteria(
        expected_outputs: list[str],
        acceptance_criteria: list[str],
    ) -> list[str]:
        """Every declared expected_output becomes its own acceptance
        criterion (P1.3b) — deterministically, without checking whether some
        other criterion's wording already talks about it. Reuses
        `_unique_planning_entries` for the merge, so calling this again on an
        already-derived list (e.g. plan -> split -> consolidation) never
        duplicates anything."""
        derived = [
            Orchestrator._expected_output_criterion(output)
            for output in expected_outputs
        ]
        return Orchestrator._unique_planning_entries(
            [*acceptance_criteria, *derived]
        )

    @staticmethod
    def _validate_work(content: dict[str, Any]) -> WorkArtifactProposal:
        try:
            return WorkArtifactProposal.model_validate(content)
        except ValidationError as exc:
            raise InvalidPlan(f"Invalid workspace artifact: {exc}") from exc

    @staticmethod
    def _candidate_matches_artifact(
        proposal: WorkArtifactProposal,
        artifact: Artifact,
    ) -> bool:
        previous_files = artifact.content.get("files", [])
        if not isinstance(previous_files, list):
            return False
        previous = {
            str(file.get("path", "")).casefold(): str(file.get("content", ""))
            for file in previous_files
            if isinstance(file, dict) and str(file.get("path", "")).strip()
        }
        current = {
            file.path.casefold(): file.content
            for file in proposal.files
        }
        return bool(previous) and current == previous

    def _colliding_dependency_paths(
        self,
        item: WorkItem,
        proposal: WorkArtifactProposal,
    ) -> set[str]:
        allowed_work_item_ids = {item.id, *self._transitive_dependency_ids(item)}
        by_id = {
            candidate.id: candidate
            for candidate in self.repository.list_work_items(item.project_id)
        }
        cancelled_work_item_ids = {
            candidate.id
            for candidate in by_id.values()
            if candidate.status is WorkItemStatus.CANCELLED
        }
        owners: dict[str, str] = {}
        for artifact in self.repository.list_artifacts(item.project_id):
            if artifact.work_item_id in allowed_work_item_ids:
                continue
            if artifact.work_item_id in cancelled_work_item_ids:
                # A cancelled task's candidate was never integrated (e.g. it
                # was superseded by a split into subtasks); its claim on a
                # path is moot and must not block the replacement work.
                continue
            files = artifact.content.get("files", [])
            if not isinstance(files, list):
                continue
            for file in files:
                if not isinstance(file, dict):
                    continue
                path = str(file.get("path", "")).strip().casefold()
                if path:
                    owners[path] = artifact.work_item_id
        colliding: set[str] = set()
        for file in proposal.files:
            key = file.path.casefold()
            owner_id = owners.get(key)
            if owner_id is None:
                continue
            owner = by_id.get(owner_id)
            # Intentional sharing: both sides tagged with the same grouping
            # and this candidate isn't claiming sole ("exclusive") ownership
            # of the path — e.g. a fragment/patch contributing to a file a
            # consolidation task (or the original exclusive owner) combines.
            # Only the CANDIDATE's own strategy is constrained here; the
            # owning task may legitimately still be "exclusive" in origin.
            if (
                owner is not None
                and item.shared_component is not None
                and item.shared_component == owner.shared_component
                and item.output_strategy is not OutputStrategy.EXCLUSIVE
            ):
                continue
            colliding.add(file.path)
        return colliding

    def _transitive_dependency_ids(self, item: WorkItem) -> set[str]:
        by_id = {
            candidate.id: candidate
            for candidate in self.repository.list_work_items(item.project_id)
        }
        seen: set[str] = set()
        pending = list(item.dependency_ids)
        while pending:
            dependency_id = pending.pop()
            if dependency_id in seen:
                continue
            seen.add(dependency_id)
            dependency = by_id.get(dependency_id)
            if dependency is not None:
                pending.extend(dependency.dependency_ids)
        return seen

    @staticmethod
    def _recovery_work_proposal(
        artifact: Artifact,
        item: WorkItem,
        *,
        approved_dependency_artifact_ids: set[str] | None = None,
    ) -> WorkArtifactProposal:
        same_task = artifact.work_item_id == item.id
        approved_dependency = (
            artifact.work_item_id in item.dependency_ids
            and artifact.id in (approved_dependency_artifact_ids or set())
        )
        if artifact.project_id != item.project_id or not (
            same_task or approved_dependency
        ):
            raise ValueError(
                "Artifact must belong to the task or an approved direct dependency"
            )
        content = {
            key: value
            for key, value in artifact.content.items()
            if key != "isolation"
        }
        return Orchestrator._validate_work(content)

    @staticmethod
    def _validate_test(content: dict[str, Any]) -> TestEvaluationProposal:
        try:
            return TestEvaluationProposal.model_validate(content)
        except ValidationError as exc:
            raise InvalidPlan(f"Invalid tester evaluation: {exc}") from exc

    @staticmethod
    def _fallback_test_evaluation(
        *,
        report_passed: bool,
        reason: str,
    ) -> TestEvaluationProposal:
        return TestEvaluationProposal(
            passed=report_passed,
            checks=[
                {
                    "name": "model_explanation_contract",
                    "passed": False,
                    "evidence": reason[:2000] or "Invalid tester explanation",
                }
            ],
            summary=(
                "La explicación del modelo fue inválida; el resultado se derivó "
                "exclusivamente de checksums, aislamiento y perfiles locales."
            ),
        )

    @staticmethod
    def _work_declaration_matches_item(
        proposal: WorkArtifactProposal,
        item: WorkItem,
    ) -> bool:
        addressed = set(proposal.acceptance_criteria_addressed)
        current_criteria = set(item.acceptance_criteria)
        return not current_criteria or bool(addressed.intersection(current_criteria))

    @staticmethod
    def _technical_evidence_passed(
        *,
        file_verified: bool,
        profiles_passed: bool,
    ) -> bool:
        return file_verified and profiles_passed

    @staticmethod
    def _semantic_review_criteria(
        criteria: list[str],
        *,
        report_passed: bool,
    ) -> list[str]:
        if report_passed:
            return criteria
        return criteria[:3]

    @staticmethod
    def _apply_technical_review_gate(
        proposal: ReviewEvaluationProposal,
        criteria: list[str],
        *,
        report_passed: bool,
        validation_checks: list[dict[str, object]],
    ) -> ReviewEvaluationProposal:
        if report_passed:
            return proposal
        failed_profiles = [
            check
            for check in validation_checks
            if not bool(check.get("passed"))
        ]
        details: list[str] = []
        for check in failed_profiles:
            output = str(
                check.get("stderr")
                or check.get("stdout")
                or ""
            ).strip()
            profile = str(check.get("profile") or "unknown")
            details.append(f"{profile}: {output[:1200] or 'sin detalle'}")
        reason = "La compuerta técnica fija rechazó la entrega"
        if details:
            reason = f"{reason}. {'; '.join(details)}"
        return ReviewEvaluationProposal(
            verdict=ReviewVerdict.CHANGES_REQUESTED.value,
            reasons=[reason, *proposal.reasons][:20],
            acceptance_results={criterion: False for criterion in criteria},
        )

    @staticmethod
    def _review_payload(
        artifact: dict[str, Any],
        workspace_file_contents: list[dict[str, Any]],
        acceptance_criteria: list[str],
        dependency_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Keep technical model opinions out of the semantic review."""
        semantic_artifact = {
            key: value
            for key, value in artifact.items()
            if key
            not in {
                "acceptance_criteria_addressed",
                "evidence",
                "isolation",
            }
        }
        return {
            "artifact": semantic_artifact,
            "workspace_file_contents": workspace_file_contents,
            "acceptance_criteria": acceptance_criteria,
            "dependency_artifacts": dependency_artifacts,
        }

    @staticmethod
    def _validate_review(
        content: dict[str, Any],
        acceptance_criteria: list[str],
    ) -> ReviewEvaluationProposal:
        proposal = Orchestrator._validate_review_fragment(
            content,
            acceptance_criteria,
        )
        if set(proposal.acceptance_results) != set(acceptance_criteria):
            raise InvalidPlan(
                "Reviewer acceptance_results must cover every acceptance criterion exactly"
            )
        return Orchestrator._merge_review_fragments(
            [proposal],
            acceptance_criteria,
        )

    @staticmethod
    def _validate_review_fragment(
        content: dict[str, Any],
        allowed_criteria: list[str],
    ) -> ReviewEvaluationProposal:
        try:
            proposal = ReviewEvaluationProposal.model_validate(content)
        except ValidationError as exc:
            raise InvalidPlan(f"Invalid reviewer evaluation: {exc}") from exc
        unexpected = set(proposal.acceptance_results) - set(allowed_criteria)
        if unexpected:
            raise InvalidPlan(
                "Reviewer acceptance_results contained unexpected criteria: "
                + "; ".join(sorted(unexpected))
            )
        return proposal

    @staticmethod
    def _validate_focused_review(
        content: dict[str, Any],
        criterion: str,
    ) -> ReviewEvaluationProposal:
        try:
            return Orchestrator._validate_review(content, [criterion])
        except InvalidPlan as original_error:
            raw_results = content.get("acceptance_results")
            result_values = (
                [
                    value
                    for value in raw_results.values()
                    if isinstance(value, bool)
                ]
                if isinstance(raw_results, dict)
                else []
            )
            raw_value: bool | None = None
            if result_values and all(
                value is result_values[0]
                for value in result_values
            ):
                raw_value = result_values[0]
            raw_verdict = content.get("verdict")
            if raw_value is None and raw_verdict in {
                ReviewVerdict.APPROVED.value,
                ReviewVerdict.CHANGES_REQUESTED.value,
            }:
                raw_value = raw_verdict == ReviewVerdict.APPROVED.value
            if raw_value is None:
                raise original_error
            raw_reasons = content.get("reasons")
            reasons = (
                [
                    reason
                    for reason in raw_reasons
                    if isinstance(reason, str) and reason.strip()
                ]
                if isinstance(raw_reasons, list)
                else []
            )
            if not reasons:
                raise original_error
            return ReviewEvaluationProposal(
                verdict=(
                    ReviewVerdict.APPROVED.value
                    if raw_value
                    else ReviewVerdict.CHANGES_REQUESTED.value
                ),
                reasons=(
                    reasons[:20]
                ),
                acceptance_results={criterion: raw_value},
            )

    @staticmethod
    def _recover_initial_review_fragment(
        content: dict[str, Any],
        allowed_criteria: list[str],
    ) -> tuple[list[ReviewEvaluationProposal], str | None]:
        try:
            fragment = Orchestrator._validate_review_fragment(
                content,
                allowed_criteria,
            )
        except InvalidPlan as exc:
            return [], str(exc)
        return [fragment], None

    @staticmethod
    def _merge_review_fragments(
        proposals: list[ReviewEvaluationProposal],
        acceptance_criteria: list[str],
    ) -> ReviewEvaluationProposal:
        combined_results: dict[str, bool] = {}
        combined_reasons: list[str] = []
        for proposal in proposals:
            combined_results.update(proposal.acceptance_results)
            for reason in proposal.reasons:
                if reason not in combined_reasons:
                    combined_reasons.append(reason)
        if set(combined_results) != set(acceptance_criteria):
            raise InvalidPlan(
                "Reviewer acceptance_results must cover every acceptance criterion exactly"
            )
        expected_verdict = (
            ReviewVerdict.APPROVED.value
            if all(combined_results.values())
            else ReviewVerdict.CHANGES_REQUESTED.value
        )
        if not combined_reasons:
            combined_reasons = [
                (
                    "Todos los criterios de aceptación fueron satisfechos."
                    if expected_verdict == ReviewVerdict.APPROVED.value
                    else "Al menos un criterio de aceptación no fue satisfecho."
                )
            ]
        return ReviewEvaluationProposal(
            verdict=expected_verdict,
            reasons=combined_reasons[:20],
            acceptance_results={
                criterion: combined_results[criterion]
                for criterion in acceptance_criteria
            },
        )
