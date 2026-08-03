from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import TypeVar

T = TypeVar("T")


@dataclass
class AttemptTiming:
    """Timestamps for a single pass through `ResourceScheduler.run`.

    Populated even when the caller is cancelled (e.g. by an enclosing
    `asyncio.wait_for` timeout): each `finally` below runs on the way out
    regardless of whether the call succeeded, raised, or was cancelled, so a
    caller can inspect timing on every path, not just the happy one.
    """

    queue_started_at: float | None = None
    acquired_at: float | None = None
    queue_ended_at: float | None = None
    generation_ended_at: float | None = None

    @property
    def queue_wait_ms(self) -> int | None:
        end = self.acquired_at if self.acquired_at is not None else self.queue_ended_at
        if self.queue_started_at is None or end is None:
            return None
        return max(0, round((end - self.queue_started_at) * 1000))

    @property
    def generation_ms(self) -> int | None:
        # None means generation never started (still queued when cut off), not
        # that it took zero time.
        if self.acquired_at is None or self.generation_ended_at is None:
            return None
        return max(0, round((self.generation_ended_at - self.acquired_at) * 1000))


class ResourceScheduler:
    """Separates logical readiness from conservative physical model concurrency."""

    def __init__(self, max_concurrent_model_calls: int = 1) -> None:
        if max_concurrent_model_calls < 1:
            raise ValueError("max_concurrent_model_calls must be positive")
        self._semaphore = asyncio.Semaphore(max_concurrent_model_calls)
        self.limit = max_concurrent_model_calls
        self.active = 0
        self.peak_active = 0
        self.queued = 0

    async def run(
        self,
        call: Callable[[], Awaitable[T]],
        timing: AttemptTiming | None = None,
    ) -> T:
        timing = timing if timing is not None else AttemptTiming()
        self.queued += 1
        timing.queue_started_at = perf_counter()
        acquired = False
        try:
            async with self._semaphore:
                acquired = True
                self.queued -= 1
                timing.acquired_at = perf_counter()
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
                try:
                    return await call()
                finally:
                    timing.generation_ended_at = perf_counter()
                    self.active -= 1
        finally:
            # Cancelled while still waiting for the semaphore (e.g. an outer
            # asyncio.wait_for timeout firing before acquisition): the block
            # above never ran, so `queued` was never decremented on its own.
            if not acquired:
                self.queued -= 1
                timing.queue_ended_at = perf_counter()

    def snapshot(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "active": self.active,
            "queued": self.queued,
            "peak_active": self.peak_active,
        }
