from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import AgentRole, RiskLevel, RunOutcome, WorkItemStatus
from agentarium.domain.models import (
    AgentRun,
    Artifact,
    Milestone,
    Project,
    ResourceUsage,
    WorkItem,
    new_id,
)
from agentarium.execution import WorkArtifactProposal, WorkspaceFileProposal
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService

# P3.4 (ADR 0034): Orchestrator._evaluate_candidate refuses to let a project
# marked `imported=True` execute delivered code, at whichever of the 3 real
# entry points reaches it (_execute_work_item, re_evaluate_artifact,
# evaluate_operator_candidate). test_validation_profiles.py proves the gate
# itself (ValidationProfileExecutor.validate(allow_project_code_execution=)));
# this file proves the wiring survives contact with the real orchestrator:
# the materialized Artifact is kept, tester/reviewer are skipped, the task
# never retries, and — just as importantly — the block never fires for a
# task that never needed execution in the first place. Regression for the
# imported=False (default) path: test_script_execution_contract_integration.py's
# test_execute_work_item_honors_a_manually_declared_execution_contract already
# exercises _evaluate_candidate end to end and needed no change for this PR.

_SENTINEL_TOOL_CONTENT = (
    "with open('should_not_exist.txt', 'w', encoding='utf-8') as handle:\n"
    "    handle.write('esto no deberia existir')\n"
)


def _project(service: ApplicationService, *, imported: bool) -> Project:
    return service.repository.create_project(
        Project(
            title="Imported repo" if imported else "Greenfield project",
            goal="Trabajar sobre un repositorio importado",
            imported=imported,
        )
    )


def _milestone(service: ApplicationService, project_id: str) -> Milestone:
    milestone = Milestone(
        project_id=project_id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    return milestone


def _executable_work_item(
    project_id: str, milestone_id: str, *, max_attempts: int = 3
) -> WorkItem:
    return WorkItem(
        project_id=project_id,
        milestone_id=milestone_id,
        title="Entregar tool.py",
        description="Entregar una herramienta ejecutable",
        expected_outputs=["tool.py"],
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
        max_attempts=max_attempts,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )


def _static_work_item(project_id: str, milestone_id: str) -> WorkItem:
    return WorkItem(
        project_id=project_id,
        milestone_id=milestone_id,
        title="Entregar notas",
        description="Entregar documentacion sin necesidad de ejecucion",
        expected_outputs=["notes.md"],
        acceptance_criteria=["El documento incluye una seccion de notas clara"],
        max_attempts=3,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )


def _work_response(*, artifact_type: str, path: str, content: str) -> ProviderResponse:
    payload = {
        "artifact_type": artifact_type,
        "title": "Entrega",
        "summary": "Resultado determinista de la entrega.",
        "quality": "verified",
        "files": [{"path": path, "content": content, "purpose": "Entrega"}],
    }
    raw = json.dumps(payload)
    return ProviderResponse(
        content=payload,
        raw_text=raw,
        prompt_characters=len(raw),
        response_characters=len(raw),
    )


def _blocked_events(service: ApplicationService, project_id: str) -> list[dict[str, object]]:
    return [
        event
        for event in service.repository.list_events(project_id)
        if event["action"] == "imported_project_execution_blocked"
    ]


@pytest.mark.asyncio
async def test_imported_project_blocks_script_execution_but_keeps_artifact(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(service, imported=True)
    milestone = _milestone(service, project.id)
    item = _executable_work_item(project.id, milestone.id)
    service.repository.add_work_item(item)

    original_generate = MockProvider.generate
    role_calls: list[str] = []

    async def controlled_generate(self, request, agent):  # type: ignore[no-untyped-def]
        role_calls.append(request.operation)
        if request.operation == "work" and request.work_item_id == item.id:
            return _work_response(
                artifact_type="code", path="tool.py", content=_SENTINEL_TOOL_CONTENT
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_generate)

    await service.orchestrator._execute_work_item(item, "test-correlation")

    reloaded = service.repository.get_work_item(item.id)
    assert reloaded.status is WorkItemStatus.FAILED
    # Never retried despite max_attempts=3: attempt_count stayed at 1, and
    # TESTER/CRITICAL_REVIEWER were never invoked for a candidate already
    # condemned by authority.
    assert reloaded.attempt_count == 1
    assert role_calls == ["work"]

    blocked = _blocked_events(service, project.id)
    assert len(blocked) == 1
    assert blocked[0]["work_item_id"] == item.id

    # The Artifact this attempt produced stays on record -- P4.2 needs it
    # even though the code inside it never ran.
    artifacts = service.repository.list_artifacts(project.id)
    assert len(artifacts) == 1
    assert artifacts[0].work_item_id == item.id

    # The real proof, not just the terminal status: the script never ran.
    assert (
        list(service.orchestrator.workspace.workspace_root.rglob("should_not_exist.txt"))
        == []
    )


@pytest.mark.asyncio
async def test_imported_project_static_only_task_matches_non_imported_outcome(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported_project = _project(service, imported=True)
    imported_milestone = _milestone(service, imported_project.id)
    imported_item = _static_work_item(imported_project.id, imported_milestone.id)
    service.repository.add_work_item(imported_item)

    plain_project = _project(service, imported=False)
    plain_milestone = _milestone(service, plain_project.id)
    plain_item = _static_work_item(plain_project.id, plain_milestone.id)
    service.repository.add_work_item(plain_item)

    original_generate = MockProvider.generate
    tracked_ids = {imported_item.id, plain_item.id}

    async def controlled_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id in tracked_ids:
            return _work_response(
                artifact_type="documentation",
                path="docs/notes.md",
                content="# Notas\n\nDocumento sin necesidad de ejecucion.\n",
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_generate)

    await service.orchestrator._execute_work_item(imported_item, "test-correlation-imported")
    await service.orchestrator._execute_work_item(plain_item, "test-correlation-plain")

    imported_result = service.repository.get_work_item(imported_item.id)
    plain_result = service.repository.get_work_item(plain_item.id)

    # Not rejected merely for being imported: a task that never asked to
    # execute anything reaches the same outcome either way.
    assert imported_result.status is not WorkItemStatus.FAILED
    assert imported_result.status == plain_result.status
    assert _blocked_events(service, imported_project.id) == []


@pytest.mark.asyncio
async def test_imported_project_blocks_recover_artifact_path(
    service: ApplicationService,
) -> None:
    project = _project(service, imported=True)
    milestone = _milestone(service, project.id)
    item = _executable_work_item(project.id, milestone.id)
    service.repository.add_work_item(item)

    run = AgentRun(
        project_id=project.id,
        work_item_id=item.id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="test-fixture",
        provider="local",
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="Fixture de recuperacion",
        output_summary="Fixture de recuperacion",
        resource_usage=ResourceUsage(model="test-fixture", provider="local"),
        correlation_id=new_id(),
    )
    service.repository.add_agent_run(run)

    artifact = Artifact(
        project_id=project.id,
        work_item_id=item.id,
        agent_run_id=run.id,
        artifact_type="code",
        title="Candidato previo",
        content={
            "artifact_type": "code",
            "title": "Herramienta",
            "summary": "Entrega una herramienta",
            "quality": "verified",
            "files": [
                {
                    "path": "tool.py",
                    "content": _SENTINEL_TOOL_CONTENT,
                    "purpose": "Herramienta",
                }
            ],
        },
        file_paths=["project/tool.py"],
        checksum="placeholder",
    )
    service.repository.add_artifact(artifact)

    # re_evaluate_artifact requires a caller-chosen except-clause path
    # distinct from _execute_work_item's (see engine.py) -- the block must
    # be proven here too, not just inferred from the other entry point.
    result = await service.orchestrator.re_evaluate_artifact(item.id, artifact.id)

    assert result.status is WorkItemStatus.FAILED
    assert result.attempt_count == 1
    assert len(_blocked_events(service, project.id)) == 1
    assert (
        list(service.orchestrator.workspace.workspace_root.rglob("should_not_exist.txt"))
        == []
    )


@pytest.mark.asyncio
async def test_imported_project_blocks_operator_candidate_path(
    service: ApplicationService,
) -> None:
    project = _project(service, imported=True)
    milestone = _milestone(service, project.id)
    item = _executable_work_item(project.id, milestone.id)
    service.repository.add_work_item(item)

    proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Candidato de operador",
        summary="Entrega manual de un operador local",
        quality="verified",
        files=[
            WorkspaceFileProposal(
                path="tool.py",
                content=_SENTINEL_TOOL_CONTENT,
                purpose="Herramienta",
            )
        ],
    )

    # A third, distinct except-clause path (see engine.py) -- same reason
    # this needs its own direct test as re_evaluate_artifact above.
    result = await service.orchestrator.evaluate_operator_candidate(item.id, proposal)

    assert result.status is WorkItemStatus.FAILED
    assert result.attempt_count == 1
    assert len(_blocked_events(service, project.id)) == 1
    assert (
        list(service.orchestrator.workspace.workspace_root.rglob("should_not_exist.txt"))
        == []
    )
