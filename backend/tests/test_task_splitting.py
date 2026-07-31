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


@pytest.mark.asyncio
async def test_a_subtask_that_exhausts_its_own_attempts_does_not_split_again(
    service: ApplicationService,
) -> None:
    project = service.create_project("Objetivo con subtarea agotada")
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
    )
    service.repository.add_work_item(item)

    split = await service.orchestrator._attempt_split(
        item, new_id(), "Se agotaron los intentos"
    )

    assert split is False
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.CHANGES_REQUESTED
    assert not any(
        candidate.title.startswith("[subtarea] Parte ya dividida")
        for candidate in service.repository.list_work_items(project.id)
        if candidate.id != item.id
    )


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
async def test_split_falls_back_to_failed_when_the_proposal_drops_a_criterion(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    assert service.repository.get_work_item(item.id).status is WorkItemStatus.FAILED
    assert not any(
        candidate.title.startswith("[subtarea] ") or candidate.title.startswith(
            "Consolidar subtareas:"
        )
        for candidate in service.repository.list_work_items(project.id)
        if candidate.id != item.id
    )
