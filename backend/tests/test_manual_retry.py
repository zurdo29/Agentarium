from __future__ import annotations

from typing import Any

import pytest
from agentarium.domain.enums import ProjectStatus, WorkItemStatus
from agentarium.domain.models import Milestone, WorkItem
from agentarium.services import ApplicationService


def test_manual_retry_grants_one_attempt_after_budget_exhaustion(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado pequeño y verificable")
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    work_item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Implementar",
        description="Crear el resultado",
        expected_outputs=["resultado"],
        acceptance_criteria=["Existe"],
        max_attempts=1,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(work_item)
    work_item = service.repository.transition_work_item(
        work_item.id,
        WorkItemStatus.ASSIGNED,
    )
    work_item = service.repository.transition_work_item(
        work_item.id,
        WorkItemStatus.RUNNING,
    )
    work_item = service.repository.increment_attempt(work_item.id)
    service.repository.transition_work_item(work_item.id, WorkItemStatus.FAILED)

    retried = service.retry_work_item(work_item.id)

    assert retried.status is WorkItemStatus.READY
    assert retried.attempt_count == 1
    assert retried.max_attempts == 2
    events = service.repository.list_events(project.id)
    assert any(event["action"] == "attempt_budget_extended" for event in events)


def test_manual_retry_extends_recovered_ready_task_after_interruption(
    service: ApplicationService,
) -> None:
    project = service.create_project("Recuperar una ejecución interrumpida")
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    work_item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Recuperar",
        description="Continuar el resultado",
        expected_outputs=["resultado"],
        acceptance_criteria=["Existe"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.READY,
    )
    service.repository.add_work_item(work_item)

    retried = service.retry_work_item(work_item.id)

    assert retried.status is WorkItemStatus.READY
    assert retried.attempt_count == 1
    assert retried.max_attempts == 2
    events = service.repository.list_events(project.id)
    assert any(event["action"] == "attempt_budget_extended" for event in events)


@pytest.mark.asyncio
async def test_artifact_recovery_extends_budget_and_readies_failed_project(
    service: ApplicationService,
    monkeypatch: Any,
) -> None:
    project = service.create_project("Recover a verified candidate")
    milestone = Milestone(
        project_id=project.id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    work_item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Repair",
        description="Recover candidate",
        expected_outputs=["result"],
        acceptance_criteria=["Works"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.FAILED,
    )
    service.repository.add_work_item(work_item)
    service.repository.update_project_status(project.id, ProjectStatus.FAILED)

    async def recover(work_item_id: str, artifact_id: str) -> WorkItem:
        assert work_item_id == work_item.id
        assert artifact_id == "candidate"
        return service.repository.get_work_item(work_item_id)

    monkeypatch.setattr(service.orchestrator, "re_evaluate_artifact", recover)

    recovered = await service.recover_artifact(work_item.id, "candidate")

    assert recovered.status is WorkItemStatus.READY
    assert recovered.max_attempts == 2
    assert service.repository.get_project(project.id).status is ProjectStatus.READY


@pytest.mark.asyncio
async def test_operator_candidate_uses_the_same_audited_evaluation_path(
    service: ApplicationService,
    monkeypatch: Any,
) -> None:
    project = service.create_project("Repair a generated game")
    milestone = Milestone(
        project_id=project.id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    work_item = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Repair",
        description="Submit operator candidate",
        expected_outputs=["game"],
        acceptance_criteria=["Works"],
        max_attempts=1,
        attempt_count=1,
        status=WorkItemStatus.FAILED,
    )
    service.repository.add_work_item(work_item)
    service.repository.update_project_status(project.id, ProjectStatus.FAILED)
    captured: dict[str, Any] = {}

    async def evaluate(work_item_id: str, proposal: Any) -> WorkItem:
        captured["work_item_id"] = work_item_id
        captured["proposal"] = proposal
        return service.repository.get_work_item(work_item_id)

    monkeypatch.setattr(
        service.orchestrator,
        "evaluate_operator_candidate",
        evaluate,
    )

    result = await service.submit_candidate(
        work_item.id,
        title="Playable game",
        summary="Operator repair",
        files=[
            {
                "path": "game.js",
                "content": "const score = 0;",
                "purpose": "Game logic",
            }
        ],
    )

    assert result.status is WorkItemStatus.READY
    assert result.max_attempts == 2
    assert captured["work_item_id"] == work_item.id
    assert captured["proposal"].acceptance_criteria_addressed == ["Works"]
    assert captured["proposal"].files[0].path == "game.js"


def test_completed_task_can_create_an_audited_rework_revision(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear una entrega y revisarla")
    milestone = Milestone(
        project_id=project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(milestone)
    original = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Implementar",
        description="Crear el resultado",
        expected_outputs=["resultado"],
        acceptance_criteria=["Funciona"],
        status=WorkItemStatus.PASSED,
    )
    service.repository.add_work_item(original)
    service.repository.transition_work_item(
        original.id,
        WorkItemStatus.COMPLETED,
    )
    service.repository.update_project_status(
        project.id,
        ProjectStatus.COMPLETED,
    )

    revision = service.rework_work_item(
        original.id,
        "La comprobación independiente encontró un fallo.",
    )

    assert revision.status is WorkItemStatus.READY
    assert revision.dependency_ids == [original.id]
    assert service.repository.get_project(project.id).status is ProjectStatus.READY
    events = service.repository.list_events(project.id)
    assert any(event["action"] == "task_rework_created" for event in events)


def test_rework_can_extend_acceptance_criteria(
    service: ApplicationService,
) -> None:
    project = service.create_project("Create and extend a delivery")
    milestone = Milestone(
        project_id=project.id,
        title="Delivery",
        description="Local delivery",
        order=0,
    )
    service.repository.add_milestone(milestone)
    original = WorkItem(
        project_id=project.id,
        milestone_id=milestone.id,
        title="Implement",
        description="Create result",
        expected_outputs=["result"],
        acceptance_criteria=["Works"],
        status=WorkItemStatus.PASSED,
    )
    service.repository.add_work_item(original)
    service.repository.transition_work_item(
        original.id,
        WorkItemStatus.COMPLETED,
    )
    service.repository.update_project_status(
        project.id,
        ProjectStatus.COMPLETED,
    )

    revision = service.rework_work_item(
        original.id,
        "Add a complete game loop.",
        [
            "Walls block movement",
            "Lives decrease on collision",
            "Victory and restart are available",
            "Walls block movement",
        ],
    )

    assert revision.acceptance_criteria == [
        "Works",
        "Walls block movement",
        "Lives decrease on collision",
        "Victory and restart are available",
    ]
