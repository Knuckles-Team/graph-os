"""Fleet-event ingress: the ``FleetEvent`` KG node stays the triage
pipeline's source of truth (not a native-only label, so unaffected by the
connected engine's row guard); the event is additionally mirrored onto the
append-only ``fleet.events`` broker stream (EG-5), which requires the
``fleet:events`` scope on the publishing identity (eg-workitem WRAPUP §3d).
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import msgpack
import pytest

from graph_os.gateway import fleet_events


class _FakeEngine:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    def add_node(self, node_id: str, node_type: str, properties=None) -> None:
        self.nodes[node_id] = {"type": node_type, **(properties or {})}


class _FakeBroker:
    def __init__(self) -> None:
        self.declared: list[tuple[str, int | None, int | None]] = []
        self.published: list[tuple[str, bytes, int]] = []

    async def stream_declare(self, stream, *, max_messages=None, max_age_ms=None):
        self.declared.append((stream, max_messages, max_age_ms))
        return "ok"

    async def stream_publish(self, stream, payload, now_ms):
        self.published.append((stream, payload, now_ms))
        return len(self.published) - 1


class _FakeClient:
    def __init__(self, broker: _FakeBroker) -> None:
        self.broker = broker

    def use_verified_context(self, claims: Any) -> Any:
        return contextlib.nullcontext()


class _FakeApplication:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    def graph_client(self, graph: str) -> Any:
        return self._client


def _session(tenant: str = "tenant-a", scopes=("fleet:events",)) -> Any:
    scope_set = frozenset(scopes)

    def _require_scope(scope: str) -> None:
        if scope not in scope_set:
            raise PermissionError(f"missing scope {scope!r}")

    return SimpleNamespace(
        tenant=tenant,
        scopes=scope_set,
        require_scope=_require_scope,
        engine_verified_context=lambda: {"tenant": tenant},
    )


def _bind(monkeypatch, *, client: _FakeClient, session: Any):
    monkeypatch.setattr(
        "agent_utilities.security.request_identity.system_write_session",
        lambda *a, **k: session,
    )
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application", lambda: _FakeApplication(client)
    )


@pytest.fixture(autouse=True)
def _reset_stream_declared_flag(monkeypatch):
    monkeypatch.setattr(fleet_events, "_fleet_stream_declared", False)


@pytest.mark.asyncio
async def test_persist_event_writes_node_and_mirrors_to_stream(monkeypatch):
    engine = _FakeEngine()
    broker = _FakeBroker()
    _bind(monkeypatch, client=_FakeClient(broker), session=_session())

    event = fleet_events.FleetEvent(
        source="uptime-kuma",
        severity="critical",
        subject="caddy-mcp",
        status="down",
        summary="monitor down",
    )
    event_id = await fleet_events.persist_event(engine, event)

    assert event_id in engine.nodes
    assert engine.nodes[event_id]["type"] == "FleetEvent"
    assert engine.nodes[event_id]["subject"] == "caddy-mcp"

    assert broker.declared == [
        (
            fleet_events.FLEET_EVENTS_STREAM,
            fleet_events._FLEET_STREAM_MAX_MESSAGES,
            fleet_events._FLEET_STREAM_MAX_AGE_MS,
        )
    ]
    assert len(broker.published) == 1
    stream, raw, _now_ms = broker.published[0]
    assert stream == fleet_events.FLEET_EVENTS_STREAM
    decoded = msgpack.unpackb(raw, raw=False)
    assert decoded["event_id"] == event_id
    assert decoded["subject"] == "caddy-mcp"


@pytest.mark.asyncio
async def test_persist_event_survives_a_stream_mirror_failure(monkeypatch):
    """The KG node write (triage's source of truth) must never be lost just
    because the additive stream mirror couldn't publish."""
    engine = _FakeEngine()

    def _boom(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "agent_utilities.security.request_identity.system_write_session", _boom
    )

    event = fleet_events.FleetEvent(
        source="alertmanager",
        severity="warning",
        subject="vector-mcp",
        status="firing",
        summary="disk pressure",
    )
    event_id = await fleet_events.persist_event(engine, event)

    assert event_id in engine.nodes


@pytest.mark.asyncio
async def test_persist_event_mirror_fails_closed_without_fleet_events_scope(
    monkeypatch,
):
    """A caller identity missing ``fleet:events`` never reaches the broker
    call at all (EG would refuse it anyway; failing here is cheaper and the
    node write still survives)."""
    engine = _FakeEngine()
    broker = _FakeBroker()
    _bind(monkeypatch, client=_FakeClient(broker), session=_session(scopes=()))

    event = fleet_events.FleetEvent(
        source="portainer",
        severity="info",
        subject="registry.example.invalid",
        status="unknown",
        summary="check",
    )
    event_id = await fleet_events.persist_event(engine, event)

    assert event_id in engine.nodes
    assert broker.published == []


@pytest.mark.asyncio
async def test_stream_declare_is_called_at_most_once(monkeypatch):
    engine = _FakeEngine()
    broker = _FakeBroker()
    _bind(monkeypatch, client=_FakeClient(broker), session=_session())

    for _ in range(3):
        await fleet_events.persist_event(
            engine,
            fleet_events.FleetEvent(
                source="generic",
                severity="info",
                subject="svc",
                status="up",
                summary="ok",
            ),
        )

    assert len(broker.declared) == 1
    assert len(broker.published) == 3
