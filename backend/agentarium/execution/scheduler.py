from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


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

    async def run(self, call: Callable[[], Awaitable[T]]) -> T:
        self.queued += 1
        async with self._semaphore:
            self.queued -= 1
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                return await call()
            finally:
                self.active -= 1

    def snapshot(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "active": self.active,
            "queued": self.queued,
            "peak_active": self.peak_active,
        }
