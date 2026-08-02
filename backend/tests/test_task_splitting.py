from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import OutputStrategy, ProjectStatus, WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem, new_id
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService


def _milestone(service: ApplicationService, project_id: str) -> Milestone:
    milestone = Milestone(
        project_id=project_id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    return milestone


@pytest.mark.asyncio
async def test_task_splits_into_subtasks_after_exhausting_retries_and_project_completes(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.orchestrator.plan_project(project.id)
    items = service.repository.list_work_items(project.id)
    target = next(
        item for item in items if item.title == "Producir el artefacto principal"
    )
    downstream = next(item for item in items if target.id in item.dependency_ids)

    original_generate = MockProvider.generate

    async def always_reject_target(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "review" and request.work_item_id == target.id:
            content = {
                "verdict": "changes_requested",
                "reasons": ["Forzado a rechazar en el test"],
                "acceptance_results": {
                    criterion: False
                    for criterion in request.payload["acceptance_criteria"]
                },
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", always_reject_target)

    result = await service.run_project(project.id)

    if result.status is not ProjectStatus.COMPLETED:
        for debug_item in service.repository.list_work_items(project.id):
            print(
                "WORK_ITEM",
                debug_item.title,
                debug_item.status,
                debug_item.attempt_count,
                "err=",
                debug_item.last_error,
            )

    assert result.status is ProjectStatus.COMPLETED

    final_items = service.repository.list_work_items(project.id)
    final_target = next(item for item in final_items if item.id == target.id)
    assert final_target.status is WorkItemStatus.CANCELLED

    children = [
        item for item in final_items if item.title.startswith("[subtarea] ")
    ]
    assert len(children) >= 2
    assert all(item.status is WorkItemStatus.COMPLETED for item in children)
    assert all(item.dependency_ids == target.dependency_ids for item in children)
    assert all(item.output_strategy is OutputStrategy.FRAGMENT for item in children)
    assert all(item.shared_component is not None for item in children)
    assert len({item.shared_component for item in children}) == 1

    consolidation = next(
        item for item in final_items if item.title.startswith("Consolidar subtareas:")
    )
    assert consolidation.status is WorkItemStatus.COMPLETED
    assert set(consolidation.dependency_ids) == {child.id for child in children}
    assert consolidation.output_strategy is OutputStrategy.CONSOLIDATION
    assert consolidation.shared_component == children[0].shared_component

    final_downstream = next(
        item for item in final_items if item.id == downstream.id
    )
    assert target.id not in final_downstream.dependency_ids
    assert consolidation.id in final_downstream.dependency_ids

    events = service.repository.list_events(project.id)
    assert any(event["action"] == "task_split_created" for event in events)


@pytest.mark.asyncio
async def test_split_is_a_no_op_when_nothing_depends_on_the_task(
    service: ApplicationService,
) -> None:
    project = service.create_project("Objetivo local sin dependientes")
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea amplia",
        description="Cubre dos criterios independientes",
        expected_outputs=["resultado"],
        acceptance_criteria=["Primer criterio", "Segundo criterio"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
    )
    service.repository.add_work_item(item)

    split = await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    assert split is True
    updated = service.repository.get_work_item(item.id)
    assert updated.status is WorkItemStatus.CANCELLED
    children = [
        candidate
        for candidate in service.repository.list_work_items(project.id)
        if candidate.title.startswith("[subtarea] ")
    ]
    assert len(children) >= 2


@pytest.mark.parametrize(
    ("title", "strategy"),
    [
        # The depth is what stops the split, not the shape of the title: a
        # consolidation carries no "[subtarea] " prefix and used to slip
        # through, splitting itself into a second generation.
        ("[subtarea] Parte ya dividida", OutputStrategy.FRAGMENT),
        ("Consolidar subtareas: Tarea amplia", OutputStrategy.CONSOLIDATION),
        ("Un titulo cualquiera sin prefijo", OutputStrategy.FRAGMENT),
    ],
)
@pytest.mark.asyncio
async def test_anything_born_of_a_split_fails_instead_of_splitting_again(
    service: ApplicationService,
    title: str,
    strategy: OutputStrategy,
) -> None:
    project = service.create_project("Objetivo con linaje ya dividido")
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title=title,
        description="Ya es producto de una división anterior",
        expected_outputs=["resultado"],
        acceptance_criteria=["Primer criterio", "Segundo criterio"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
        output_strategy=strategy,
        split_depth=1,
    )
    service.repository.add_work_item(item)

    await service.orchestrator._on_attempts_exhausted(
        item, new_id(), "Se agotaron los intentos"
    )

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.FAILED
    assert len(service.repository.list_work_items(project.id)) == 1
    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_depth_exhausted" in events
    assert "task_split_created" not in events


@pytest.mark.asyncio
async def test_a_failed_split_child_stays_repairable_by_hand(
    service: ApplicationService,
) -> None:
    # Terminal for the orchestrator, not for the operator: the existing
    # retry path still reopens it, and exhausting it again still does not
    # start a second decomposition round.
    project = service.create_project("Objetivo con hija reparable")
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="[subtarea] Parte ya dividida",
        description="Ya es producto de una división anterior",
        expected_outputs=["resultado"],
        acceptance_criteria=["Primer criterio", "Segundo criterio"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
        split_depth=1,
    )
    service.repository.add_work_item(item)

    await service.orchestrator._on_attempts_exhausted(
        item, new_id(), "Se agotaron los intentos"
    )
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.FAILED

    reopened = service.retry_work_item(item.id)
    assert reopened.status is WorkItemStatus.READY
    assert reopened.max_attempts > reopened.attempt_count
    assert reopened.split_depth == 1

    # Reopening does not reset the lineage: exhausting it again still refuses
    # to decompose.
    assert (
        await service.orchestrator._attempt_split(
            reopened, new_id(), "Se agotaron los intentos otra vez"
        )
        is False
    )
    assert len(service.repository.list_work_items(project.id)) == 1


@pytest.mark.asyncio
async def test_a_single_criterion_task_is_not_split(
    service: ApplicationService,
) -> None:
    project = service.create_project("Objetivo con un solo criterio")
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea acotada",
        description="Un único criterio, nada que dividir",
        expected_outputs=["resultado"],
        acceptance_criteria=["Único criterio"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
    )
    service.repository.add_work_item(item)

    split = await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    assert split is False


@pytest.mark.asyncio
async def test_a_proposal_that_drops_a_criterion_falls_back_to_a_partition(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Used to abandon the split and fail the task. Since ADR 0026 the parent's
    # criteria are handed out by the orchestrator, so a proposal that repeats
    # one criterion and drops another is repaired instead of thrown away.
    project = service.create_project("Objetivo con propuesta incompleta")
    milestone = _milestone(service, project.id)
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Tarea amplia",
        description="Cubre dos criterios independientes",
        expected_outputs=["resultado"],
        acceptance_criteria=["Primer criterio", "Segundo criterio"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
    )
    service.repository.add_work_item(item)

    original_generate = MockProvider.generate

    async def incomplete_decompose(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "decompose":
            content = {
                "subtasks": [
                    {
                        "title": "Sólo una parte",
                        "description": "No cubre todos los criterios originales",
                        "expected_outputs": ["resultado"],
                        "acceptance_criteria": ["Primer criterio"],
                    },
                    {
                        "title": "Otra parte irrelevante",
                        "description": "Tampoco cubre el criterio faltante",
                        "expected_outputs": ["resultado"],
                        "acceptance_criteria": ["Primer criterio"],
                    },
                ]
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", incomplete_decompose)

    await service.orchestrator._on_attempts_exhausted(
        item, new_id(), "Se agotaron los intentos"
    )

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.CANCELLED
    children = [
        candidate
        for candidate in service.repository.list_work_items(project.id)
        if candidate.title.startswith("[subtarea] ")
    ]
    assert len(children) == 2
    # Every parent criterion covered exactly once, with the parent's own text.
    assigned = [criterion for child in children for criterion in child.acceptance_criteria]
    assert sorted(assigned) == ["Primer criterio", "Segundo criterio"]

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_criteria_partitioned" in events


def _stub_decompose(
    monkeypatch: pytest.MonkeyPatch,
    subtasks: list[tuple[str, str, str]],
) -> None:
    """Force a decompose response of (title, criterion, expected output).

    owned_paths is deliberately never declared and output_strategy is left at
    its "exclusive" default: that is exactly what qwen2.5-coder:7b returned in
    workspace 487c5194, and what the split has to handle on its own.
    """
    original_generate = MockProvider.generate

    async def decompose(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation != "decompose":
            return await original_generate(self, request, agent)
        content = {
            "subtasks": [
                {
                    "title": title,
                    "description": f"Subtarea {title}",
                    "expected_outputs": [output],
                    "acceptance_criteria": [criterion],
                }
                for title, criterion, output in subtasks
            ]
        }
        raw = json.dumps(content)
        return ProviderResponse(
            content=content,
            raw_text=raw,
            prompt_characters=len(raw),
            response_characters=len(raw),
        )

    monkeypatch.setattr(MockProvider, "generate", decompose)


def _exhausted_item(
    service: ApplicationService,
    project_id: str,
    criteria: list[str],
) -> WorkItem:
    milestone = _milestone(service, project_id)
    item = WorkItem(
        project_id=project_id,
        milestone_id=milestone.id,
        title="Implementación de la API REST",
        description="Cubre varios endpoints independientes",
        expected_outputs=["api.py"],
        acceptance_criteria=criteria,
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.CHANGES_REQUESTED,
    )
    service.repository.add_work_item(item)
    return item


def _children_by_title(
    service: ApplicationService,
    project_id: str,
) -> dict[str, WorkItem]:
    return {
        candidate.title.removeprefix("[subtarea] "): candidate
        for candidate in service.repository.list_work_items(project_id)
        if candidate.title.startswith("[subtarea] ")
    }


def _consolidation(service: ApplicationService, project_id: str) -> WorkItem:
    return next(
        candidate
        for candidate in service.repository.list_work_items(project_id)
        if candidate.title.startswith("Consolidar subtareas:")
    )


@pytest.mark.asyncio
async def test_subtasks_claiming_the_same_file_are_chained_instead_of_parallel(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con archivo único compartido")
    item = _exhausted_item(service, project.id, ["Listar libros", "Agregar libros"])
    _stub_decompose(
        monkeypatch,
        [
            ("Listar", "Listar libros", "api.py"),
            ("Agregar", "Agregar libros", "api.py"),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children_by_title(service, project.id)
    head, follower = children["Listar"], children["Agregar"]

    # The path was only ever named in expected_outputs; the split promotes it.
    assert head.owned_paths == ["api.py"]
    assert follower.owned_paths == ["api.py"]

    assert head.status is WorkItemStatus.READY
    assert head.output_strategy is OutputStrategy.FRAGMENT
    assert head.dependency_ids == list(item.dependency_ids)

    assert follower.status is WorkItemStatus.BLOCKED
    assert follower.output_strategy is OutputStrategy.PATCH
    assert follower.dependency_ids == [*item.dependency_ids, head.id]

    assert head.shared_component == follower.shared_component

    # Only the tail: the head is already covered transitively through it.
    consolidation = _consolidation(service, project.id)
    assert consolidation.dependency_ids == [follower.id]

    # One automatic split per lineage: the depth is persisted on everything
    # the split produced, so none of them can start a second round.
    assert item.split_depth == 0
    assert head.split_depth == 1
    assert follower.split_depth == 1
    assert consolidation.split_depth == 1
    assert service.repository.get_work_item(head.id).split_depth == 1

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_chained_overlapping_paths" in events


@pytest.mark.asyncio
async def test_only_the_overlapping_subtasks_are_chained(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo con solapamiento parcial")
    item = _exhausted_item(
        service,
        project.id,
        ["Listar libros", "Agregar libros", "Modelar datos"],
    )
    _stub_decompose(
        monkeypatch,
        [
            ("Listar", "Listar libros", "api.py"),
            ("Agregar", "Agregar libros", "api.py"),
            ("Modelar", "Modelar datos", "models.py"),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children_by_title(service, project.id)
    head, follower, independent = (
        children["Listar"],
        children["Agregar"],
        children["Modelar"],
    )

    assert follower.dependency_ids == [*item.dependency_ids, head.id]
    assert follower.output_strategy is OutputStrategy.PATCH
    assert follower.status is WorkItemStatus.BLOCKED

    # A subtask writing its own file keeps running in parallel with the chain.
    assert independent.dependency_ids == list(item.dependency_ids)
    assert independent.output_strategy is OutputStrategy.FRAGMENT
    assert independent.status is WorkItemStatus.READY
    assert independent.owned_paths == ["models.py"]

    consolidation = _consolidation(service, project.id)
    assert set(consolidation.dependency_ids) == {follower.id, independent.id}


@pytest.mark.asyncio
async def test_overlap_grouping_is_transitive(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A shares api.py with B, B shares models.py with C. Chaining only the
    # direct pairs would put B in two chains and let one of them overwrite it.
    project = service.create_project("Objetivo con solapamiento encadenado")
    item = _exhausted_item(
        service,
        project.id,
        ["Listar libros", "Agregar libros", "Modelar datos"],
    )
    original_generate = MockProvider.generate

    async def decompose(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation != "decompose":
            return await original_generate(self, request, agent)
        content = {
            "subtasks": [
                {
                    "title": "Listar",
                    "description": "Endpoints de lectura",
                    "expected_outputs": ["api.py"],
                    "acceptance_criteria": ["Listar libros"],
                },
                {
                    "title": "Agregar",
                    "description": "Endpoint de alta y su modelo",
                    "expected_outputs": ["api.py", "models.py"],
                    "acceptance_criteria": ["Agregar libros"],
                },
                {
                    "title": "Modelar",
                    "description": "Modelo de datos",
                    "expected_outputs": ["models.py"],
                    "acceptance_criteria": ["Modelar datos"],
                },
            ]
        }
        raw = json.dumps(content)
        return ProviderResponse(
            content=content,
            raw_text=raw,
            prompt_characters=len(raw),
            response_characters=len(raw),
        )

    monkeypatch.setattr(MockProvider, "generate", decompose)

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children_by_title(service, project.id)
    first, second, third = (
        children["Listar"],
        children["Agregar"],
        children["Modelar"],
    )
    assert second.dependency_ids == [*item.dependency_ids, first.id]
    assert third.dependency_ids == [*item.dependency_ids, second.id]
    assert third.output_strategy is OutputStrategy.PATCH

    consolidation = _consolidation(service, project.id)
    assert consolidation.dependency_ids == [third.id]


@pytest.mark.asyncio
async def test_subtasks_writing_distinct_files_stay_parallel(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Objetivo sin solapamiento")
    item = _exhausted_item(service, project.id, ["Listar libros", "Agregar libros"])
    _stub_decompose(
        monkeypatch,
        [
            ("Listar", "Listar libros", "routes/list.py"),
            ("Agregar", "Agregar libros", "routes/create.py"),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children_by_title(service, project.id)
    assert all(
        child.status is WorkItemStatus.READY for child in children.values()
    )
    assert all(
        child.output_strategy is OutputStrategy.FRAGMENT
        for child in children.values()
    )
    assert all(
        child.dependency_ids == list(item.dependency_ids)
        for child in children.values()
    )

    consolidation = _consolidation(service, project.id)
    assert set(consolidation.dependency_ids) == {
        child.id for child in children.values()
    }

    events = {
        event["action"] for event in service.repository.list_events(project.id)
    }
    assert "task_split_chained_overlapping_paths" not in events


@pytest.mark.asyncio
async def test_prose_expected_outputs_do_not_chain_unrelated_subtasks(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two subtasks both declaring "resultado" is not a file claim: chaining
    # them would serialize work that never touches the same file.
    project = service.create_project("Objetivo con entregables en prosa")
    item = _exhausted_item(service, project.id, ["Primer criterio", "Segundo criterio"])
    _stub_decompose(
        monkeypatch,
        [
            ("Primera", "Primer criterio", "resultado"),
            ("Segunda", "Segundo criterio", "resultado"),
        ],
    )

    assert await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    children = _children_by_title(service, project.id)
    assert all(
        child.status is WorkItemStatus.READY for child in children.values()
    )
    assert all(child.owned_paths == [] for child in children.values())
