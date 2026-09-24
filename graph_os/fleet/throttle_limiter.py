"""A resizable concurrency limiter for one fleet child (EH-406).

``asyncio.Semaphore`` cannot shrink, but an error-budget throttle must narrow a
child's admission the moment its EG ``CapacityCell`` ceiling drops, and give it
back as the ceiling recovers. This limiter holds a ``limit`` that can be
resized at any time: a narrowed limit admits nothing new until enough
in-flight calls finish (held calls are never cancelled), and a widened limit
wakes queued callers in FIFO order at once.
"""

from __future__ import annotations

import asyncio
from collections import deque

__all__ = ["ResizableLimiter"]


class ResizableLimiter:
    """FIFO slot limiter whose limit can be narrowed or widened live."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("a limiter admits at least one call")
        self._limit = limit
        self._held = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def held(self) -> int:
        return self._held

    def locked(self) -> bool:
        """Whether a new caller would have to wait."""
        return self._held >= self._limit or bool(self._waiters)

    async def acquire(self) -> None:
        """Take one slot, waiting in FIFO order while the limiter is full."""
        if not self.locked():
            self._held += 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            self._abandon(waiter)
            raise

    def release(self) -> None:
        """Return one slot and admit the next waiter the limit allows."""
        self._held -= 1
        self._wake()

    def resize(self, limit: int) -> None:
        """Set a new limit (at least 1); wider limits admit waiters now."""
        self._limit = max(1, limit)
        self._wake()

    def _abandon(self, waiter: asyncio.Future[None]) -> None:
        if waiter.done() and not waiter.cancelled():
            # Granted just as the caller gave up: hand the slot on.
            self.release()
            return
        waiter.cancel()
        if waiter in self._waiters:
            self._waiters.remove(waiter)

    def _wake(self) -> None:
        while self._waiters and self._held < self._limit:
            waiter = self._waiters.popleft()
            if waiter.done():
                continue
            self._held += 1
            waiter.set_result(None)
