import pytest
from agentarium.domain.enums import ProjectStatus, WorkItemStatus
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService

_STUCK_STATES = {
    WorkItemStatus.ASSIGNED,
    WorkItemStatus.RUNNING,
    WorkItemStatus.AWAITING_REVIEW,
}


@pytest.mark.asyncio
async def test_tester_provider_failure_fails_the_task_instead_of_crashing(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    async def failing_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "test":
            raise TimeoutError("Simulated tester provider timeout")
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", failing_generate)

    project = service.create_project("Crear un resultado persistente")
    result = await service.run_project(project.id)

    assert result.status is ProjectStatus.FAILED
    items = service.repository.list_work_items(project.id)
    assert not any(item.status in _STUCK_STATES for item in items)
    failed = next(item for item in items if item.status is WorkItemStatus.FAILED)
    assert failed.attempt_count == failed.max_attempts


@pytest.mark.asyncio
async def test_director_provider_failure_fails_planning_instead_of_crashing(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    async def failing_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "brief":
            raise TimeoutError("Simulated director provider timeout")
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", failing_generate)

    project = service.create_project("Crear un resultado persistente")
    result = await service.run_project(project.id)

    assert result.status is ProjectStatus.FAILED
    assert not service.repository.list_work_items(project.id)


@pytest.mark.asyncio
async def test_technical_manager_provider_failure_fails_planning_instead_of_crashing(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    async def failing_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "plan":
            raise TimeoutError("Simulated technical manager provider timeout")
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", failing_generate)

    project = service.create_project("Crear un resultado persistente")
    result = await service.run_project(project.id)

    assert result.status is ProjectStatus.FAILED
    assert not service.repository.list_work_items(project.id)


@pytest.mark.asyncio
async def test_reviewer_provider_failure_fails_the_task_instead_of_crashing(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockProvider.generate

    async def failing_generate(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "review":
            raise TimeoutError("Simulated reviewer provider timeout")
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", failing_generate)

    project = service.create_project("Crear un resultado persistente")
    result = await service.run_project(project.id)

    assert result.status is ProjectStatus.FAILED
    items = service.repository.list_work_items(project.id)
    assert not any(item.status in _STUCK_STATES for item in items)
    failed = next(item for item in items if item.status is WorkItemStatus.FAILED)
    assert failed.attempt_count == failed.max_attempts
