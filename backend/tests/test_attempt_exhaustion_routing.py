from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import AgentRole, RunOutcome, WorkItemStatus
from agentarium.domain.models import (
    AgentRun,
    Artifact,
    Milestone,
    ResourceUsage,
    WorkItem,
    new_id,
)
from agentarium.execution import (
    WorkspaceInfrastructureRejected,
    WorkspaceRejected,
    WorkspaceSecurityRejected,
)
from agentarium.isolation import IsolationError
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.orchestration.engine import InvalidPlan, Orchestrator
from agentarium.services import ApplicationService

CANDIDATE_FILES = [
    {
        "path": "library/api.py",
        "content": "def listar():\n    return []\n",
        "purpose": "Endpoints de la biblioteca",
    }
]


def _work_content(files: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "artifact_type": "Code",
        "title": "API de biblioteca",
        "summary": "Implementa los endpoints pedidos por la tarea.",
        "quality": "verified",
        "evidence": ["Se ejecutaron los endpoints a mano."],
        "acceptance_criteria_addressed": [],
        "files": files if files is not None else CANDIDATE_FILES,
    }


def _exhausting_item(
    service: ApplicationService,
    project_id: str,
    *,
    attempt_count: int = 2,
    max_attempts: int = 3,
) -> WorkItem:
    """A task one attempt away from exhaustion, with two independent criteria."""
    milestone = Milestone(
        project_id=project_id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    item = WorkItem(
        project_id=project_id,
        milestone_id=milestone.id,
        title="Implementar la API de biblioteca",
        description="Cubre dos criterios independientes",
        expected_outputs=["library/api.py"],
        acceptance_criteria=["Listar libros", "Agregar libros"],
        max_attempts=max_attempts,
        attempt_count=attempt_count,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(item)
    return item


def _seed_prior_candidate(
    service: ApplicationService,
    item: WorkItem,
    files: list[dict[str, str]],
) -> None:
    """Persist a rejected candidate so a repeat is detectable."""
    run = AgentRun(
        project_id=item.project_id,
        work_item_id=item.id,
        agent_role=AgentRole.IMPLEMENTATION_WORKER,
        model="mock",
        provider="mock",
        attempt=max(1, item.attempt_count),
        outcome=RunOutcome.ARTIFACT_DELIVERED,
        input_summary="Intento previo",
        output_summary="Intento previo",
        resource_usage=ResourceUsage(model="mock", provider="mock"),
        correlation_id=new_id(),
    )
    service.repository.add_agent_run(run)
    service.repository.add_artifact(
        Artifact(
            project_id=item.project_id,
            work_item_id=item.id,
            agent_run_id=run.id,
            artifact_type="Code",
            title="API de biblioteca",
            content={"files": files},
            file_paths=[file["path"] for file in files],
        )
    )


def _fixed_work_response(
    monkeypatch: pytest.MonkeyPatch,
    content: dict[str, object],
) -> None:
    original_generate = MockProvider.generate

    async def generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation != "work":
            return await original_generate(self, request, agent)
        raw = json.dumps(content)
        return ProviderResponse(
            content=content,
            raw_text=raw,
            prompt_characters=len(raw),
            response_characters=len(raw),
        )

    monkeypatch.setattr(MockProvider, "generate", generate)


def _split_happened(service: ApplicationService, project_id: str) -> bool:
    return any(
        event["action"] == "task_split_created"
        for event in service.repository.list_events(project_id)
    )


# --- classification at the source -------------------------------------------


def test_escaping_paths_are_classified_as_security(
    service: ApplicationService,
    tmp_path,
) -> None:
    materializer = service.orchestrator.workspace
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(WorkspaceSecurityRejected):
        materializer._contained_file(root, "../escaped.py")
    # A security rejection is still a WorkspaceRejected for every existing
    # caller that only knows the base class.
    with pytest.raises(WorkspaceRejected):
        materializer._contained_file(root, "../escaped.py")


def test_candidate_failure_policy_separates_the_three_kinds() -> None:
    policy = Orchestrator._candidate_failure_policy
    assert policy(InvalidPlan("candidato repetido")) == (True, True)
    assert policy(WorkspaceRejected("demasiados archivos")) == (True, True)
    assert policy(WorkspaceInfrastructureRejected("no se pudo escribir")) == (True, False)
    assert policy(IsolationError("git falló")) == (True, False)
    assert policy(WorkspaceSecurityRejected("se escapó del directorio")) == (False, False)


# --- correctable errors reach the split -------------------------------------


@pytest.mark.asyncio
async def test_repeated_candidate_at_the_last_attempt_triggers_a_split(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con candidato repetido")
    item = _exhausting_item(service, project.id)
    _seed_prior_candidate(service, item, CANDIDATE_FILES)
    _fixed_work_response(monkeypatch, _work_content())

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.CANCELLED
    assert "byte-for-byte identical" in str(updated.last_error)
    assert _split_happened(service, project.id)
    children = [
        candidate
        for candidate in service.repository.list_work_items(project.id)
        if candidate.title.startswith("[subtarea] ")
    ]
    assert len(children) >= 2


@pytest.mark.asyncio
async def test_model_generated_invalid_path_at_the_last_attempt_triggers_a_split(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact shape qwen2.5-coder:7b produced in workspace 8be5cde9: the
    # model prefixed its own output path with the internal workspaces root.
    project = service.create_project("Objetivo con path invalido")
    item = _exhausting_item(service, project.id)
    _fixed_work_response(
        monkeypatch,
        _work_content(
            [
                {
                    "path": f"workspaces/{project.id}/api.py",
                    "content": "def listar():\n    return []\n",
                    "purpose": "Endpoints de la biblioteca",
                }
            ]
        ),
    )

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.CANCELLED
    assert _split_happened(service, project.id)


# --- non-correctable errors never reach the split ---------------------------


@pytest.mark.asyncio
async def test_security_violation_fails_without_retrying_or_splitting(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two attempts still available: a boundary violation must not consume them
    # and must not decompose the task either.
    project = service.create_project("Objetivo con violacion de seguridad")
    item = _exhausting_item(service, project.id, attempt_count=0, max_attempts=3)
    _fixed_work_response(monkeypatch, _work_content())

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceSecurityRejected("Workspace file escaped its authorized directory")

    monkeypatch.setattr(service.orchestrator.workspace, "stage", refuse)

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.FAILED
    assert updated.attempt_count == 1
    assert not _split_happened(service, project.id)
    assert not any(
        candidate.title.startswith("[subtarea] ")
        for candidate in service.repository.list_work_items(project.id)
    )


@pytest.mark.asyncio
async def test_infrastructure_failure_retries_but_never_splits(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con fallo de infraestructura")
    item = _exhausting_item(service, project.id)
    _fixed_work_response(monkeypatch, _work_content())

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceInfrastructureRejected(
            "Workspace file could not be installed: api.py"
        )

    monkeypatch.setattr(service.orchestrator.workspace, "stage", refuse)

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.FAILED
    assert not _split_happened(service, project.id)


@pytest.mark.asyncio
async def test_infrastructure_failure_still_retries_while_attempts_remain(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con fallo transitorio")
    item = _exhausting_item(service, project.id, attempt_count=0, max_attempts=3)
    _fixed_work_response(monkeypatch, _work_content())

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceInfrastructureRejected(
            "Workspace file could not be installed: api.py"
        )

    monkeypatch.setattr(service.orchestrator.workspace, "stage", refuse)

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.READY
    assert updated.attempt_count == 1


# --- attempt accounting and worktree hygiene --------------------------------


@pytest.mark.asyncio
async def test_the_rejected_worktree_is_discarded_before_children_are_created(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A content-limit rejection is raised by stage(), i.e. after the worktree
    # already exists — the only shape where the discard ordering is observable.
    # (A repeated candidate is caught before prepare(), so no worktree is ever
    # created on that path.)
    project = service.create_project("Objetivo con worktree a descartar")
    item = _exhausting_item(service, project.id)
    _fixed_work_response(monkeypatch, _work_content())

    order: list[str] = []
    original_discard = service.orchestrator.isolation.discard
    original_add_work_item = service.repository.add_work_item

    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise WorkspaceRejected("Workspace action exceeds the 12-file limit")

    async def spy_discard(session):  # type: ignore[no-untyped-def]
        order.append("discard")
        return await original_discard(session)

    def spy_add_work_item(work_item):  # type: ignore[no-untyped-def]
        if work_item.title.startswith("[subtarea] "):
            order.append("child")
        return original_add_work_item(work_item)

    monkeypatch.setattr(service.orchestrator.workspace, "stage", refuse)
    monkeypatch.setattr(service.orchestrator.isolation, "discard", spy_discard)
    monkeypatch.setattr(service.repository, "add_work_item", spy_add_work_item)

    await service.orchestrator._execute_work_item(item, new_id())

    assert "child" in order, order
    assert "discard" in order, order
    # The worktree is released before the first subtask exists.
    assert order.index("discard") < order.index("child"), order


@pytest.mark.asyncio
async def test_a_split_run_increments_the_attempt_exactly_once(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con conteo de intentos")
    item = _exhausting_item(service, project.id, attempt_count=2, max_attempts=3)
    _seed_prior_candidate(service, item, CANDIDATE_FILES)
    _fixed_work_response(monkeypatch, _work_content())

    await service.orchestrator._execute_work_item(item, new_id())

    updated = service.repository.get_work_item(item.id)
    assert updated.attempt_count == 3
    assert _split_happened(service, project.id)
    # Children start their own budget, they do not inherit the exhausted one.
    children = [
        candidate
        for candidate in service.repository.list_work_items(project.id)
        if candidate.title.startswith("[subtarea] ")
    ]
    assert all(child.attempt_count == 0 for child in children)
