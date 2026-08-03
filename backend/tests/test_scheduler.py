import asyncio

import pytest
from agentarium.execution.scheduler import AttemptTiming, ResourceScheduler


@pytest.mark.asyncio
async def test_scheduler_limits_real_inference_concurrency() -> None:
    scheduler = ResourceScheduler(max_concurrent_model_calls=1)
    simultaneous = 0
    peak = 0

    async def work(value: int) -> int:
        nonlocal simultaneous, peak
        simultaneous += 1
        peak = max(peak, simultaneous)
        await asyncio.sleep(0.01)
        simultaneous -= 1
        return value

    results = await asyncio.gather(
        *(scheduler.run(lambda value=value: work(value)) for value in range(4))
    )

    assert results == [0, 1, 2, 3]
    assert peak == 1
    assert scheduler.peak_active == 1
    assert scheduler.active == 0


@pytest.mark.asyncio
async def test_scheduler_records_real_queue_wait_behind_another_call() -> None:
    scheduler = ResourceScheduler(max_concurrent_model_calls=1)
    first_timing = AttemptTiming()
    second_timing = AttemptTiming()

    async def slow() -> str:
        await asyncio.sleep(0.1)
        return "first"

    async def fast() -> str:
        return "second"

    first = asyncio.create_task(scheduler.run(slow, first_timing))
    await asyncio.sleep(0.02)  # let `first` acquire before `second` starts queuing
    second = asyncio.create_task(scheduler.run(fast, second_timing))
    await asyncio.gather(first, second)

    assert first_timing.queue_wait_ms is not None
    assert first_timing.queue_wait_ms < 50
    assert second_timing.queue_wait_ms is not None
    assert second_timing.queue_wait_ms >= 50  # waited out most of `first`'s ~100ms


async def _queue_then_cancel() -> tuple[
    ResourceScheduler, AttemptTiming, "asyncio.Task[None]", asyncio.Event
]:
    """Common setup: one call holds the only slot, a second is cancelled while
    still queued behind it (never reaches the semaphore)."""
    scheduler = ResourceScheduler(max_concurrent_model_calls=1)
    holder_timing = AttemptTiming()
    waiter_timing = AttemptTiming()
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    async def never_called() -> None:
        raise AssertionError("must not run while still queued behind the holder")

    holder = asyncio.create_task(scheduler.run(hold, holder_timing))
    await asyncio.sleep(0.02)  # let `holder` acquire the semaphore first

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(scheduler.run(never_called, waiter_timing), timeout=0.05)

    return scheduler, waiter_timing, holder, release


@pytest.mark.asyncio
async def test_scheduler_cancelled_while_queued_leaves_generation_ms_null() -> None:
    _scheduler, waiter_timing, holder, release = await _queue_then_cancel()

    assert waiter_timing.acquired_at is None
    assert waiter_timing.generation_ms is None
    assert waiter_timing.queue_wait_ms is not None

    release.set()
    await holder


@pytest.mark.asyncio
async def test_scheduler_resets_queued_and_active_after_cancellation() -> None:
    scheduler, _waiter_timing, holder, release = await _queue_then_cancel()

    assert scheduler.queued == 0
    assert scheduler.active == 1  # the holder is still running, that slot is legitimate

    release.set()
    await holder

    assert scheduler.active == 0
    assert scheduler.queued == 0


@pytest.mark.asyncio
async def test_scheduler_records_generation_time_when_cut_off_mid_call() -> None:
    scheduler = ResourceScheduler(max_concurrent_model_calls=1)
    timing = AttemptTiming()

    async def slow_generation() -> None:
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(scheduler.run(slow_generation, timing), timeout=0.05)

    assert timing.acquired_at is not None
    assert timing.queue_wait_ms is not None
    assert timing.queue_wait_ms < 30  # nothing else competing, acquired right away
    assert timing.generation_ms is not None
    assert 20 <= timing.generation_ms < 500  # cut off partway through the 1s sleep
    assert scheduler.active == 0
