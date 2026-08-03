from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from agentarium.agents.roles import RoleExecutionError, RoleRunner
from agentarium.domain.enums import AgentRole
from agentarium.domain.models import AgentDefinition
from agentarium.execution.scheduler import ResourceScheduler
from agentarium.llm import ModelRequest, ProviderResponse


class _FakeCatalog:
    def __init__(self, definition: AgentDefinition) -> None:
        self._definition = definition

    def get(self, role: AgentRole) -> AgentDefinition:
        return self._definition


class _FakeProviders:
    def __init__(self, provider: object) -> None:
        self._provider = provider

    def get(self, name: str) -> object:
        return self._provider


class _ScriptedProvider:
    """Replays one behavior per call to `generate`, in order."""

    def __init__(self, behaviors: list[Callable[[], Awaitable[ProviderResponse]]]) -> None:
        self._behaviors = iter(behaviors)

    async def generate(
        self, request: ModelRequest, definition: AgentDefinition
    ) -> ProviderResponse:
        behavior = next(self._behaviors)
        return await behavior()


def _definition(*, max_retries: int = 0, timeout_seconds: int = 5) -> AgentDefinition:
    return AgentDefinition(
        role=AgentRole.IMPLEMENTATION_WORKER,
        provider="fake",
        model="fake-model",
        temperature=0.1,
        context_limit=1000,
        output_token_limit=100,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        tools=[],
        allowed_directories=[],
        authority="workspace_write",
        execution_budget=1,
    )


def _request() -> ModelRequest:
    return ModelRequest(
        operation="implement",
        project_id="proj-1",
        work_item_id="item-1",
        attempt=1,
        payload={"goal": "test"},
    )


async def _ok(delay: float = 0.0) -> ProviderResponse:
    if delay:
        await asyncio.sleep(delay)
    return ProviderResponse(
        content={"ok": True},
        raw_text="done",
        prompt_characters=40,
        response_characters=8,
    )


async def _fail(delay: float = 0.0) -> ProviderResponse:
    if delay:
        await asyncio.sleep(delay)
    raise RuntimeError("simulated provider failure")


async def _must_not_run() -> ProviderResponse:
    raise AssertionError("must not run while another job holds the semaphore")


@pytest.mark.asyncio
async def test_success_persists_queue_wait_and_generation_in_agent_run() -> None:
    provider = _ScriptedProvider([lambda: _ok(0.01)])
    runner = RoleRunner(
        _FakeCatalog(_definition()), _FakeProviders(provider), ResourceScheduler()
    )

    _response, run = await runner.run(
        AgentRole.IMPLEMENTATION_WORKER, _request(), correlation_id="corr-1"
    )

    usage = run.resource_usage
    assert usage.queue_wait_ms is not None
    assert usage.generation_ms is not None
    assert usage.generation_ms >= 5


@pytest.mark.asyncio
async def test_failure_persists_queue_wait_and_generation_in_agent_run() -> None:
    provider = _ScriptedProvider([lambda: _fail(0.01)])
    runner = RoleRunner(
        _FakeCatalog(_definition()), _FakeProviders(provider), ResourceScheduler()
    )

    with pytest.raises(RoleExecutionError) as excinfo:
        await runner.run(AgentRole.IMPLEMENTATION_WORKER, _request(), correlation_id="corr-2")

    usage = excinfo.value.run.resource_usage
    assert usage.queue_wait_ms is not None
    # The fake provider raised only after actually running, so generation
    # happened even though the call ultimately failed.
    assert usage.generation_ms is not None
    assert usage.generation_ms >= 5


@pytest.mark.asyncio
async def test_retries_accumulate_queue_wait_and_generation() -> None:
    provider = _ScriptedProvider(
        [lambda: _fail(0.02), lambda: _fail(0.02), lambda: _ok(0.02)]
    )
    runner = RoleRunner(
        _FakeCatalog(_definition(max_retries=2)),
        _FakeProviders(provider),
        ResourceScheduler(),
    )

    _response, run = await runner.run(
        AgentRole.IMPLEMENTATION_WORKER, _request(), correlation_id="corr-3"
    )

    usage = run.resource_usage
    assert usage.errors == 2
    # Three attempts at ~20ms of generation each: the total must reflect all
    # of them, not just the last (successful) one.
    assert usage.generation_ms is not None
    assert usage.generation_ms >= 50


@pytest.mark.asyncio
async def test_timeout_while_queued_persists_queue_wait_with_null_generation() -> None:
    """The exact original P1.2 scenario: another job occupies the only
    scheduler slot, RoleRunner exhausts its own timeout waiting in queue, and
    the resulting AgentRun must show that it never reached generation."""
    scheduler = ResourceScheduler(max_concurrent_model_calls=1)
    holder_acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        holder_acquired.set()
        await release.wait()

    holder = asyncio.create_task(scheduler.run(hold))
    await holder_acquired.wait()  # deterministic: the slot is now held, no sleep needed

    provider = _ScriptedProvider([_must_not_run])
    runner = RoleRunner(
        _FakeCatalog(_definition(timeout_seconds=1)),
        _FakeProviders(provider),
        scheduler,
    )

    with pytest.raises(RoleExecutionError) as excinfo:
        await runner.run(
            AgentRole.IMPLEMENTATION_WORKER, _request(), correlation_id="corr-4"
        )

    usage = excinfo.value.run.resource_usage
    assert usage.queue_wait_ms is not None
    assert usage.generation_ms is None

    assert scheduler.queued == 0
    assert scheduler.active == 1  # the holder is still running, that slot is legitimate

    release.set()
    await holder
    assert scheduler.active == 0
