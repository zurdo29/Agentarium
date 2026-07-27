import asyncio

import pytest
from agentarium.execution.scheduler import ResourceScheduler


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
