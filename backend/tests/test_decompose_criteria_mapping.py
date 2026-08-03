from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import OutputStrategy, WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem, new_id
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.orchestration.engine import Orchestrator
from agentarium.planning import (
    DecomposeProposal,
    SubtaskProposal,
    acceptance_criteria_index,
)
from agentarium.services import ApplicationService
from pydantic import ValidationError

PARENT_CRITERIA = ["Listar libros", "Agregar libros", "Eliminar libros"]


def _subtask(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Subtarea",
        "description": "Descripción",
        "expected_outputs": ["resultado"],
        "acceptance_criteria": ["Texto reescrito por el modelo"],
    }
    base.update(overrides)
    return base


def _proposal(*subtasks: dict[str, object]) -> list[SubtaskProposal]:
    return DecomposeProposal.model_validate({"subtasks": list(subtasks)}).subtasks


def _resolve(*subtasks: dict[str, object]) -> list[list[int]] | None:
    return Orchestrator._resolve_criteria_assignment(
        PARENT_CRITERIA, _proposal(*subtasks)
    )


# --- the index and the id contract ------------------------------------------


def test_acceptance_criteria_index_numbers_from_one() -> None:
    assert acceptance_criteria_index(["Uno", "Dos"]) == [
        {"id": "ac-1", "text": "Uno"},
        {"id": "ac-2", "text": "Dos"},
    ]


def test_malformed_criteria_ids_are_rejected_by_the_contract() -> None:
    for broken in (["ac-0"], ["ac_1"], ["1"], ["AC-1x"], ["ac-"]):
        with pytest.raises(ValidationError, match="acceptance_criteria_ids"):
            SubtaskProposal.model_validate(_subtask(acceptance_criteria_ids=broken))
    # Case is normalized, not rejected.
    subtask = SubtaskProposal.model_validate(_subtask(acceptance_criteria_ids=["AC-2"]))
    assert subtask.acceptance_criteria_ids == ["ac-2"]


# --- resolving the mapping --------------------------------------------------


def test_a_complete_mapping_resolves_to_parent_positions() -> None:
    assert _resolve(
        _subtask(title="A", acceptance_criteria_ids=["ac-1", "ac-3"]),
        _subtask(title="B", acceptance_criteria_ids=["ac-2"]),
    ) == [[0, 2], [1]]


def test_an_unknown_id_is_rejected() -> None:
    assert (
        _resolve(
            _subtask(title="A", acceptance_criteria_ids=["ac-1", "ac-9"]),
            _subtask(title="B", acceptance_criteria_ids=["ac-2", "ac-3"]),
        )
        is None
    )


def test_a_repeated_id_is_rejected() -> None:
    # Same criterion handed to two subtasks: the work would be done twice and
    # something else would go missing.
    assert (
        _resolve(
            _subtask(title="A", acceptance_criteria_ids=["ac-1", "ac-2"]),
            _subtask(title="B", acceptance_criteria_ids=["ac-2", "ac-3"]),
        )
        is None
    )
    # And repeated inside a single subtask.
    assert (
        _resolve(
            _subtask(title="A", acceptance_criteria_ids=["ac-1", "ac-1"]),
            _subtask(title="B", acceptance_criteria_ids=["ac-2", "ac-3"]),
        )
        is None
    )


def test_a_missing_criterion_is_rejected() -> None:
    assert (
        _resolve(
            _subtask(title="A", acceptance_criteria_ids=["ac-1"]),
            _subtask(title="B", acceptance_criteria_ids=["ac-2"]),
        )
        is None
    )


def test_a_subtask_without_any_id_is_rejected() -> None:
    assert (
        _resolve(
            _subtask(title="A", acceptance_criteria_ids=["ac-1", "ac-2", "ac-3"]),
            _subtask(title="B", acceptance_criteria_ids=[]),
        )
        is None
    )


def test_no_ids_at_all_is_rejected() -> None:
    # The shape every model produced before this contract existed.
    assert _resolve(_subtask(title="A"), _subtask(title="B")) is None


# --- the deterministic fallback ---------------------------------------------


def test_the_partition_covers_every_criterion_exactly_once() -> None:
    partition = Orchestrator._deterministic_criteria_assignment
    assert partition(7, 3) == [[0, 1, 2], [3, 4], [5, 6]]
    assert partition(4, 4) == [[0], [1], [2], [3]]
    assert partition(2, 2) == [[0], [1]]
    for criteria_count in range(2, 12):
        for subtask_count in range(2, 5):
            groups = partition(criteria_count, subtask_count)
            flat = [position for group in groups for position in group]
            assert sorted(flat) == list(range(criteria_count))
            assert all(groups), (criteria_count, subtask_count)


def test_the_partition_never_leaves_a_child_without_criteria() -> None:
    # More subtasks proposed than there is work: the child count drops to the
    # number of criteria rather than creating a child with nothing to verify.
    assert Orchestrator._deterministic_criteria_assignment(2, 4) == [[0], [1]]
    assert Orchestrator._deterministic_criteria_assignment(3, 4) == [[0], [1], [2]]


# --- end to end through _attempt_split --------------------------------------


def _parent(service: ApplicationService, project_id: str) -> WorkItem:
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
        title="Implementar la API",
        description="Cubre tres criterios",
        expected_outputs=["api.py"],
        acceptance_criteria=list(PARENT_CRITERIA),
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
    )
    service.repository.add_work_item(item)
    return item


def _stub(monkeypatch: pytest.MonkeyPatch, subtasks: list[dict[str, object]]) -> None:
    original_generate = MockProvider.generate

    async def generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation != "decompose":
            return await original_generate(self, request, agent)
        content = {"subtasks": subtasks}
        raw = json.dumps(content)
        return ProviderResponse(
            content=content,
            raw_text=raw,
            prompt_characters=len(raw),
            response_characters=len(raw),
        )

    monkeypatch.setattr(MockProvider, "generate", generate)


def _children(service: ApplicationService, project_id: str) -> list[WorkItem]:
    return [
        candidate
        for candidate in service.repository.list_work_items(project_id)
        if candidate.title.startswith("[subtarea] ")
    ]


@pytest.mark.asyncio
async def test_children_get_the_parent_text_not_the_models_rewrite(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con mapeo correcto")
    item = _parent(service, project.id)
    _stub(
        monkeypatch,
        [
            _subtask(
                title="Lectura",
                expected_outputs=["routes/read.py"],
                acceptance_criteria=["El endpoint GET responde algo"],
                acceptance_criteria_ids=["ac-1"],
            ),
            _subtask(
                title="Escritura",
                expected_outputs=["routes/write.py"],
                acceptance_criteria=["POST y DELETE funcionan"],
                acceptance_criteria_ids=["ac-2", "ac-3"],
            ),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    by_title = {
        child.title.removeprefix("[subtarea] "): child
        for child in _children(service, project.id)
    }
    # The parent's own text, plus P1.3b's own derived criterion for each
    # child's new expected_output.
    assert by_title["Lectura"].acceptance_criteria == [
        "Listar libros",
        "El entregable esperado existe y está completo: routes/read.py",
    ]
    assert by_title["Escritura"].acceptance_criteria == [
        "Agregar libros",
        "Eliminar libros",
        "El entregable esperado existe y está completo: routes/write.py",
    ]
    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_criteria_partitioned" not in events


@pytest.mark.asyncio
async def test_a_broken_mapping_falls_back_to_the_partition(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con mapeo roto")
    item = _parent(service, project.id)
    _stub(
        monkeypatch,
        [
            _subtask(
                title="Lectura",
                expected_outputs=["routes/read.py"],
                acceptance_criteria_ids=["ac-1"],
            ),
            _subtask(
                title="Escritura",
                expected_outputs=["routes/write.py"],
                acceptance_criteria_ids=["ac-1"],  # repetido, y ac-3 sin asignar
            ),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children(service, project.id)
    assigned = [
        criterion for child in children for criterion in child.acceptance_criteria
    ]
    # Every parent criterion covered exactly once by the deterministic
    # partition, same as before P1.3b — plus each child's own derived
    # criterion for its new expected_output.
    inherited = [criterion for criterion in assigned if criterion in PARENT_CRITERIA]
    assert sorted(inherited) == sorted(PARENT_CRITERIA)
    derived = {
        "El entregable esperado existe y está completo: routes/read.py",
        "El entregable esperado existe y está completo: routes/write.py",
    }
    assert derived <= set(assigned)
    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_criteria_partitioned" in events


@pytest.mark.asyncio
async def test_missing_expected_outputs_are_inherited_and_chain_the_children(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No expected_outputs at all: min_length=1 still holds because the parent's
    # own output fills it in, and the shared inherited file chains the children
    # instead of letting them overwrite each other.
    project = service.create_project("Objetivo sin outputs declarados")
    item = _parent(service, project.id)
    _stub(
        monkeypatch,
        [
            {
                "title": "Lectura",
                "description": "Endpoints de lectura",
                "expected_outputs": [],
                "acceptance_criteria": ["Da igual"],
                "acceptance_criteria_ids": ["ac-1"],
            },
            {
                "title": "Escritura",
                "description": "Endpoints de escritura",
                "expected_outputs": [],
                "acceptance_criteria": ["Da igual"],
                "acceptance_criteria_ids": ["ac-2", "ac-3"],
            },
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    by_title = {
        child.title.removeprefix("[subtarea] "): child
        for child in _children(service, project.id)
    }
    head, follower = by_title["Lectura"], by_title["Escritura"]
    assert head.expected_outputs == ["api.py"]
    assert follower.expected_outputs == ["api.py"]
    assert head.owned_paths == ["api.py"]
    assert follower.owned_paths == ["api.py"]

    assert head.status is WorkItemStatus.READY
    assert head.output_strategy is OutputStrategy.FRAGMENT
    assert follower.status is WorkItemStatus.BLOCKED
    assert follower.output_strategy is OutputStrategy.PATCH
    assert follower.dependency_ids == [*item.dependency_ids, head.id]

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_chained_overlapping_paths" in events


@pytest.mark.asyncio
async def test_a_declared_non_file_output_is_not_replaced_by_the_parents(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "un informe" is a real answer, just not a path. It must not silently
    # inherit the parent's api.py and chain two subtasks that never share it.
    project = service.create_project("Objetivo con entregable en prosa")
    item = _parent(service, project.id)
    _stub(
        monkeypatch,
        [
            _subtask(
                title="Primera",
                expected_outputs=["un informe de lectura"],
                acceptance_criteria_ids=["ac-1"],
            ),
            _subtask(
                title="Segunda",
                expected_outputs=["un informe de escritura"],
                acceptance_criteria_ids=["ac-2", "ac-3"],
            ),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children(service, project.id)
    assert all(child.owned_paths == [] for child in children)
    assert all(child.status is WorkItemStatus.READY for child in children)
    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_chained_overlapping_paths" not in events
