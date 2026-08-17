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

# Gate-MVP.1 (ADR 0040): a candidate must stay inside the effective scope its
# own work item declared (owned_paths/expected_outputs), independent of
# whether any other task already claims the extra path. Same structural
# precedent as test_imported_project_execution_gate.py: proves the wiring at
# each of the 3 real entry points (_execute_work_item, re_evaluate_artifact,
# evaluate_operator_candidate), not just the pure predicate already covered
# in test_evaluation_contracts.py.


def _project(service: ApplicationService) -> Project:
    return service.repository.create_project(
        Project(title="Proyecto de prueba", goal="Probar la frontera de escritura")
    )


def _milestone(service: ApplicationService, project_id: str) -> Milestone:
    milestone = Milestone(
        project_id=project_id,
        title="Entrega",
        description="Hito de prueba",
        order=0,
    )
    service.repository.add_milestone(milestone)
    return milestone


def _scoped_work_item(
    project_id: str, milestone_id: str, *, title: str, expected_output: str
) -> WorkItem:
    return WorkItem(
        project_id=project_id,
        milestone_id=milestone_id,
        title=title,
        description="Entrega un único archivo declarado",
        expected_outputs=[expected_output],
        acceptance_criteria=["Existe"],
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
    )


def _work_response(*, files: list[tuple[str, str]]) -> ProviderResponse:
    payload = {
        "artifact_type": "code",
        "title": "Entrega",
        "summary": "Resultado determinista de la entrega.",
        "quality": "verified",
        "files": [
            {"path": path, "content": content, "purpose": "Entrega"}
            for path, content in files
        ],
    }
    raw = json.dumps(payload)
    return ProviderResponse(
        content=payload,
        raw_text=raw,
        prompt_characters=len(raw),
        response_characters=len(raw),
    )


def _track_prepare_calls(
    monkeypatch: pytest.MonkeyPatch, service: ApplicationService
) -> list[str]:
    calls: list[str] = []
    original_prepare = service.orchestrator.isolation.prepare

    async def tracked_prepare(project_id, work_item_id, attempt):  # type: ignore[no-untyped-def]
        calls.append(work_item_id)
        return await original_prepare(project_id, work_item_id, attempt)

    monkeypatch.setattr(service.orchestrator.isolation, "prepare", tracked_prepare)
    return calls


def _own_scope_events(
    service: ApplicationService, project_id: str
) -> list[dict[str, object]]:
    return [
        event
        for event in service.repository.list_events(project_id)
        if event["action"] == "workspace_own_scope_rejected"
    ]


@pytest.mark.asyncio
async def test_execute_work_item_rejects_a_candidate_that_writes_outside_its_own_scope(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(service)
    milestone = _milestone(service, project.id)
    item = _scoped_work_item(
        project.id, milestone.id, title="Entregar tool.py", expected_output="tool.py"
    )
    service.repository.add_work_item(item)

    prepare_calls = _track_prepare_calls(monkeypatch, service)
    original_generate = MockProvider.generate

    async def controlled_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id == item.id:
            return _work_response(
                files=[("tool.py", "print('ok')"), ("extra.py", "print('fuera de scope')")]
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_generate)

    await service.orchestrator._execute_work_item(item, "test-correlation")

    reloaded = service.repository.get_work_item(item.id)
    assert reloaded.status is WorkItemStatus.READY
    assert reloaded.attempt_count == 1
    assert prepare_calls == []
    assert service.repository.list_artifacts(project.id) == []

    events = _own_scope_events(service, project.id)
    assert len(events) == 1
    assert events[0]["metadata"]["paths"] == ["extra.py"]

    action_names = {e["action"] for e in service.repository.list_events(project.id)}
    assert "change_set_integrated" not in action_names


@pytest.mark.asyncio
async def test_re_evaluate_artifact_rejects_a_recovered_candidate_outside_its_own_scope(
    service: ApplicationService,
) -> None:
    project = _project(service)
    milestone = _milestone(service, project.id)
    item = _scoped_work_item(
        project.id, milestone.id, title="Entregar tool.py", expected_output="tool.py"
    )
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
            "title": "Entrega",
            "summary": "Entrega recuperada",
            "quality": "verified",
            "files": [
                {"path": "tool.py", "content": "print('ok')", "purpose": "p"},
                {"path": "extra.py", "content": "print('fuera')", "purpose": "p"},
            ],
        },
        file_paths=["project/tool.py", "project/extra.py"],
        checksum="placeholder",
    )
    service.repository.add_artifact(artifact)
    # The fixture Artifact above is itself outside item's own scope, so it
    # would poison list_artifacts()-based checks; not relevant here since
    # _out_of_scope_paths never queries the repository (ADR 0040) -- only
    # the recovered proposal's own files matter.

    result = await service.orchestrator.re_evaluate_artifact(item.id, artifact.id)

    assert result.status is WorkItemStatus.READY
    assert result.attempt_count == 1

    events = _own_scope_events(service, project.id)
    assert len(events) == 1
    assert events[0]["metadata"]["paths"] == ["extra.py"]

    action_names = {e["action"] for e in service.repository.list_events(project.id)}
    assert "task_split_created" not in action_names


@pytest.mark.asyncio
async def test_evaluate_operator_candidate_rejects_a_manual_candidate_outside_its_own_scope(
    service: ApplicationService,
) -> None:
    project = _project(service)
    milestone = _milestone(service, project.id)
    item = _scoped_work_item(
        project.id, milestone.id, title="Entregar tool.py", expected_output="tool.py"
    )
    service.repository.add_work_item(item)

    proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Candidato de operador",
        summary="Entrega manual de un operador local",
        quality="verified",
        files=[
            WorkspaceFileProposal(path="tool.py", content="print('ok')", purpose="p"),
            WorkspaceFileProposal(path="extra.py", content="print('fuera')", purpose="p"),
        ],
    )

    result = await service.orchestrator.evaluate_operator_candidate(item.id, proposal)

    assert result.status is WorkItemStatus.READY
    assert result.attempt_count == 1

    events = _own_scope_events(service, project.id)
    assert len(events) == 1
    assert events[0]["metadata"]["paths"] == ["extra.py"]

    action_names = {e["action"] for e in service.repository.list_events(project.id)}
    assert "task_split_created" not in action_names


@pytest.mark.asyncio
async def test_textkit_slugify_regression_frees_the_sibling_path(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the incident findings.md documents: a task whose own
    expected_outputs is one file delivers a second, undeclared file that a
    sibling task actually owns. Before Gate-MVP.1, that extra file got
    materialized into an Artifact that permanently blocked the sibling
    (_colliding_dependency_paths never releases a claim from a non-CANCELLED
    task). After Gate-MVP.1, the offending task is rejected before an
    Artifact ever exists, so the sibling never collides in the first place.
    """
    project = _project(service)
    milestone = _milestone(service, project.id)
    fix_task = _scoped_work_item(
        project.id,
        milestone.id,
        title="Modificar la función `slugify` en `textkit/slug.py`",
        expected_output="textkit/slug.py",
    )
    service.repository.add_work_item(fix_task)

    original_generate = MockProvider.generate

    async def controlled_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id == fix_task.id:
            return _work_response(
                files=[
                    ("textkit/slug.py", "def slugify(...): ..."),
                    ("tests/test_slug.py", "class TestSlugify: ..."),
                ]
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_generate)

    await service.orchestrator._execute_work_item(fix_task, "test-correlation")

    reloaded_fix_task = service.repository.get_work_item(fix_task.id)
    assert reloaded_fix_task.status is WorkItemStatus.READY
    # The undeclared file never became an Artifact -- fix_task never
    # acquired ownership of tests/test_slug.py.
    assert service.repository.list_artifacts(project.id) == []

    test_task = _scoped_work_item(
        project.id,
        milestone.id,
        title="Agregar caso de prueba en `tests/test_slug.py`",
        expected_output="tests/test_slug.py",
    )
    service.repository.add_work_item(test_task)
    test_task_proposal = WorkArtifactProposal(
        artifact_type="code",
        title="Caso de prueba",
        summary="Agrega el caso de prueba",
        quality="verified",
        files=[
            WorkspaceFileProposal(
                path="tests/test_slug.py", content="class TestSlugify: ...", purpose="p"
            )
        ],
    )

    # The real proof: the sibling's own candidate no longer collides with
    # anything, because fix_task's rejected attempt left no Artifact behind.
    assert (
        service.orchestrator._colliding_dependency_paths(test_task, test_task_proposal)
        == set()
    )
