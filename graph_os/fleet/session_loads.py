"""Bounded per-session item visibility for the dynamic multiplexer."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
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
    generations: dict[str, object] = field(default_factory=dict)
    bindings: dict[str, tuple[str, ...]] = field(default_factory=dict)
    claims: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class LoadGrant:
    """A request-local claim on one exact loaded generation, never an identity grant."""

    key: str
    item: str
    generation: object
    binding: tuple[str, ...]
    claim: object | None
    dispatched: bool = False


def _session_snapshot(session: _Session) -> list[dict[str, str | float]]:
    return [
        {"id": name, "last_use": stamp} for name, stamp in sorted(session.items.items())
    ]


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

    def _overflow_victims(
        self,
        session: _Session,
        requested: list[str],
        new: list[str],
        evict: str | None,
    ) -> list[str]:
        overflow = max(0, len(session.items) + len(new) - self.cap)
        if len(new) > self.cap:
            raise ValueError("request exceeds session cap")
        if overflow and evict != "lru":
            raise LoadCapExceeded(_session_snapshot(session))
        if evict not in {None, "lru"}:
            raise ValueError("evict must be lru")
        candidates = (name for name in session.items if name not in requested)
        victims = sorted(candidates, key=lambda name: (session.items[name], name))[
            :overflow
        ]
        if len(victims) < overflow:
            raise LoadCapExceeded(_session_snapshot(session))
        return victims

    def load(
        self,
        key: str,
        items: Iterable[str],
        *,
        evict: str | None = None,
        auto_unload: bool = False,
        bindings: Mapping[str, tuple[str, ...]] | None = None,
    ) -> dict[str, object]:
        requested = list(dict.fromkeys(items))
        if bindings is not None and any(
            not isinstance(bindings.get(name), tuple)
            or not bindings[name]
            or any(not isinstance(part, str) for part in bindings[name])
            for name in requested
        ):
            raise ValueError("load binding is incomplete")
        session = self._session(key)
        new = [name for name in requested if name not in session.items]
        victims = self._overflow_victims(session, requested, new, evict)
        for name in victims:
            self._remove(session, name)
        now = self._clock()
        for name in requested:
            session.items[name] = now
            session.generations[name] = object()
            session.bindings[name] = bindings[name] if bindings is not None else (name,)
            session.claims.pop(name, None)
            session.one_shot.discard(name)
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
        removed = sorted(set(items) & session.generations.keys())
        for name in removed:
            self._remove(session, name)
        if removed:
            session.pending_changed = True
            session.notification_sent = False
        return removed

    @staticmethod
    def _remove(session: _Session, item: str) -> None:
        session.items.pop(item, None)
        session.one_shot.discard(item)
        session.generations.pop(item, None)
        session.bindings.pop(item, None)
        session.claims.pop(item, None)

    def binding(self, key: str, item: str) -> tuple[str, ...] | None:
        return self._session(key).bindings.get(item)

    def retractable(self, key: str) -> frozenset[str]:
        """Include a consumed one-shot still waiting at its transport boundary."""
        return frozenset(self._session(key).generations)

    def acquire(self, key: str, item: str) -> LoadGrant | None:
        """Atomically reserve a one-shot; ordinary calls share a revocable generation."""
        session = self._session(key)
        if item not in session.items or item in session.claims:
            return None
        claim = object() if item in session.one_shot else None
        if claim is not None:
            session.claims[item] = claim
        return LoadGrant(
            key, item, session.generations[item], session.bindings[item], claim
        )

    def current(self, grant: LoadGrant) -> bool:
        session = self._session(grant.key)
        return (
            session.generations.get(grant.item) is grant.generation
            and session.bindings.get(grant.item) == grant.binding
            and (grant.claim is None or session.claims.get(grant.item) is grant.claim)
            and (grant.item in session.items or grant.dispatched)
        )

    def dispatch(self, grant: LoadGrant) -> bool:
        """Consume once immediately before dispatch; no await separates the fence."""
        if grant.dispatched or not self.current(grant):
            return False
        grant.dispatched = True
        session = self._session(grant.key)
        if grant.claim is not None:
            session.items.pop(grant.item, None)
            session.one_shot.discard(grant.item)
            session.pending_changed = True
            session.notification_sent = False
        else:
            session.items[grant.item] = self._clock()
        return True

    def revoke(self, grant: LoadGrant) -> list[str]:
        """Retract this generation only; never remove a later explicit reload."""
        session = self._session(grant.key)
        if session.generations.get(grant.item) is not grant.generation:
            return []
        return self.unload(grant.key, [grant.item])

    def release(self, grant: LoadGrant) -> None:
        """Release only this acquisition; never restore revoked or consumed loads."""
        session = self._session(grant.key)
        if session.generations.get(grant.item) is not grant.generation:
            return
        if grant.claim is not None and session.claims.get(grant.item) is grant.claim:
            if grant.dispatched:
                self._remove(session, grant.item)
            else:
                session.claims.pop(grant.item, None)

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
