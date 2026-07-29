import pytest
from agentarium.domain.enums import WorkItemStatus
from agentarium.services import ApplicationService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupted_status",
    [WorkItemStatus.RUNNING, WorkItemStatus.AWAITING_REVIEW],
)
async def test_interrupted_running_task_is_recovered(
    service: ApplicationService,
    interrupted_status: WorkItemStatus,
) -> None:
    project = service.create_project("Crear un resultado local pequeño")
    await service.plan_project(project.id)
    item = next(
        item
        for item in service.repository.list_work_items(project.id)
        if item.status is WorkItemStatus.READY
    )
    item = service.repository.transition_work_item(item.id, WorkItemStatus.ASSIGNED)
    item = service.repository.transition_work_item(item.id, WorkItemStatus.RUNNING)
    if interrupted_status is WorkItemStatus.AWAITING_REVIEW:
        service.repository.transition_work_item(item.id, WorkItemStatus.AWAITING_REVIEW)

    assert service.repository.recover_interrupted() == 1
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
