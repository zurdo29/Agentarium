import pytest
from agentarium.domain.enums import ProjectStatus, ReviewVerdict, WorkItemStatus
from agentarium.services import ApplicationService


@pytest.mark.asyncio
async def test_complete_vertical_flow_with_mock_correction(
    service: ApplicationService,
) -> None:
    project = service.create_project(
        "Crear un pequeño ARPG con progresión de objetos y alcance de prototipo"
    )
    completed = await service.run_project(project.id)

    assert completed.status is ProjectStatus.COMPLETED
    detail = service.project_detail(project.id)
    assert detail["project"]["brief"] is not None
    assert len(detail["work_items"]) == 3
    assert all(item["status"] == WorkItemStatus.COMPLETED.value for item in detail["work_items"])

    corrected = next(
        item for item in detail["work_items"] if item["title"] == "Producir el artefacto principal"
    )
    assert corrected["attempt_count"] == 2
    assert len(detail["artifacts"]) == 4
    assert any(
        review["verdict"] == ReviewVerdict.CHANGES_REQUESTED.value for review in detail["reviews"]
    )
    assert all(report["passed"] for report in detail["test_reports"])
    assert detail["metrics"]["tasks_completed"] == 3
    assert detail["metrics"]["tasks_rejected"] == 1
    assert detail["metrics"]["retries"] == 1

    event_actions = {
        event["action"] for event in service.repository.list_events(project.id, limit=1000)
    }
    assert {
        "project_created",
        "planning_completed",
        "review_rejected",
        "project_completed",
    }.issubset(event_actions)


@pytest.mark.asyncio
async def test_completed_work_is_persisted_and_not_repeated(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado persistente")
    await service.run_project(project.id)
    attempts_before = sum(
        item.attempt_count for item in service.repository.list_work_items(project.id)
    )

    second_result = await service.run_project(project.id)
    attempts_after = sum(
        item.attempt_count for item in service.repository.list_work_items(project.id)
    )

    assert second_result.status is ProjectStatus.COMPLETED
    assert attempts_after == attempts_before
