import pytest
from agentarium.domain.enums import WorkItemStatus
from agentarium.services import ApplicationService


@pytest.mark.asyncio
async def test_interrupted_running_task_is_recovered(
    service: ApplicationService,
) -> None:
    project = service.create_project("Crear un resultado local pequeño")
    await service.plan_project(project.id)
    item = next(
        item
        for item in service.repository.list_work_items(project.id)
        if item.status is WorkItemStatus.READY
    )
    item = service.repository.transition_work_item(item.id, WorkItemStatus.ASSIGNED)
    service.repository.transition_work_item(item.id, WorkItemStatus.RUNNING)

    assert service.repository.recover_interrupted() == 1
    assert service.repository.get_work_item(item.id).status is WorkItemStatus.READY
