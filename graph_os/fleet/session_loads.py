"""Bounded per-session item visibility for the dynamic multiplexer."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


class LoadCapExceeded(ValueError):
    def __init__(self, loaded: list[dict[str, str | float]]):
        super().__init__("LOAD_CAP_EXCEEDED")
        self.loaded = loaded


@dataclass(slots=True)
class _Session:
    items: dict[str, float] = field(default_factory=dict)
    last_access: float = 0.0
    pending_changed: bool = False
    notification_sent: bool = False
    one_shot: set[str] = field(default_factory=set)


class SessionLoads:
    def __init__(
        self,
        *,
        cap: int = 64,
        idle_ttl_seconds: int = 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not 1 <= cap <= 256:
            raise ValueError("session cap must be in 1..256")
        if idle_ttl_seconds < 1:
            raise ValueError("idle TTL must be positive")
        self.cap = cap
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, _Session] = {}

    def _session(self, key: str) -> _Session:
        now = self._clock()
        session = self._sessions.get(key)
        if session is None or now - session.last_access >= self.idle_ttl_seconds:
            session = _Session(last_access=now)
            self._sessions[key] = session
        session.last_access = now
        return session

    def loaded(self, key: str) -> frozenset[str]:
        return frozenset(self._session(key).items)

    def active_keys(self) -> tuple[str, ...]:
        """Keys whose bounded session state has not passed its idle deadline."""
        now = self._clock()
        return tuple(
            key
            for key, session in self._sessions.items()
            if now - session.last_access < self.idle_ttl_seconds
        )

    def load(
        self,
        key: str,
        items: Iterable[str],
        *,
        evict: str | None = None,
        auto_unload: bool = False,
    ) -> dict[str, object]:
        requested = list(dict.fromkeys(items))
        session = self._session(key)
        new = [name for name in requested if name not in session.items]
        overflow = max(0, len(session.items) + len(new) - self.cap)
        if len(new) > self.cap:
            raise ValueError("request exceeds session cap")
        if overflow and evict != "lru":
            raise LoadCapExceeded(
                [
                    {"id": name, "last_use": stamp}
                    for name, stamp in sorted(session.items.items())
                ]
            )
        if evict not in {None, "lru"}:
            raise ValueError("evict must be lru")
        candidates = (name for name in session.items if name not in requested)
        victims = sorted(candidates, key=lambda name: (session.items[name], name))[
            :overflow
        ]
        if len(victims) < overflow:
            raise LoadCapExceeded(
                [
                    {"id": name, "last_use": stamp}
                    for name, stamp in sorted(session.items.items())
                ]
            )
        for name in victims:
            del session.items[name]
            session.one_shot.discard(name)
        now = self._clock()
        for name in requested:
            session.items[name] = now
            if auto_unload:
                session.one_shot.add(name)
        if new or victims:
            session.pending_changed = True
            session.notification_sent = False
        return {
            "loaded": requested,
            "evicted": victims,
            "session_total": len(session.items),
        }

    def unload(self, key: str, items: Iterable[str]) -> list[str]:
        session = self._session(key)
        removed = sorted(set(items) & session.items.keys())
        for name in removed:
            del session.items[name]
            session.one_shot.discard(name)
        if removed:
            session.pending_changed = True
            session.notification_sent = False
        return removed

    def touch(self, key: str, item: str) -> bool:
        session = self._session(key)
        if item not in session.items:
            return False
        session.items[item] = self._clock()
        if item in session.one_shot:
            self.unload(key, [item])
        return True

    def notification(self, key: str, sent: bool) -> None:
        session = self._session(key)
        if sent:
            session.notification_sent = True

    def redelivered(self, key: str, sent: bool) -> None:
        session = self._session(key)
        if sent:
            session.notification_sent = True
            session.pending_changed = False

    def status(self, key: str) -> dict[str, object]:
        session = self._session(key)
        return {
            "cap": self.cap,
            "used": len(session.items),
            "loaded": [
                {"id": name, "last_use": stamp}
                for name, stamp in sorted(session.items.items())
            ],
            "list_changed_pending": session.pending_changed,
            "notification_sent": session.notification_sent,
        }
