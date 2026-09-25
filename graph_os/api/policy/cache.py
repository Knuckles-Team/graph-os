"""Short-lived policy decisions keyed by verified caller and policy revision."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

CacheKey = tuple[str, str, str, str, str]


class DecisionCache:
    def __init__(
        self, ttl_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if not 0 < ttl_seconds <= 30:
            raise ValueError("policy cache TTL must be at most 30 seconds")
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[CacheKey, tuple[float, Any]] = {}
        self._revisions: dict[str, str] = {}

    def set_revision(self, tenant: str, revision: str) -> bool:
        if not tenant or not revision:
            raise ValueError("tenant and policy revision are required")
        previous = self._revisions.get(tenant)
        changed = previous is not None and previous != revision
        if previous != revision:
            self._entries = {
                key: value for key, value in self._entries.items() if key[1] != tenant
            }
            self._revisions[tenant] = revision
        return changed

    def get(self, key: CacheKey) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires <= self._clock():
            self._entries.pop(key, None)
            return None
        return value

    def put(self, key: CacheKey, value: Any) -> None:
        self._entries[key] = (self._clock() + self._ttl, value)
