"""Fleet approvals / trace / touched over typed EG surfaces (eg-workitem
WRAPUP §3d, EG-5): a generic ``ActionApproval``/``FleetEvent`` Cypher match is
refused by the connected engine's native row guard, so these handlers now
read ``action.approval`` ControlLeases, ``ListWorkItems.metadata_match``, and
the append-only ``fleet.events`` broker stream.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import msgpack
import pytest

from graph_os.gateway import fleet


class _FakeControlLeases:
    def __init__(self, leases: list[dict[str, Any]]) -> None:
        self._leases = leases

    async def list(
        self,
        *,
        tenant: str,
        kind: str | None = None,
        status: str | None = None,
        grant_match: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        matched = [
            lease
            for lease in self._leases
            if (kind is None or lease.get("kind") == kind)
            and (status is None or lease.get("status") == status)
        ]
        return {"leases": matched[:limit], "next_cursor": None}


class _FakeWorkItems:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def list(
        self,
        *,
        tenant: str,
        cursor: str | None = None,
        limit: int = 100,
        kind: str | None = None,
        metadata_match: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        matched = self._items
        if metadata_match:
            matched = [
                item
                for item in matched
                if all(
                    (item.get("metadata") or {}).get(key) == value
                    for key, value in metadata_match.items()
                )
            ]
        return {"items": matched[:limit], "next_cursor": None}


class _FakeBroker:
    def __init__(self, messages: list[tuple[int, bytes]]) -> None:
        self._messages = messages

    async def stream_read(
        self, stream: str, *, from_offset: int = 0, max: int = 0
    ) -> list[tuple[int, bytes]]:
        batch = [m for m in self._messages if m[0] >= from_offset]
        return batch[:max] if max else batch


class _FakeClient:
    def __init__(
        self,
        *,
        control_leases: _FakeControlLeases | None = None,
        work_items: _FakeWorkItems | None = None,
        broker: _FakeBroker | None = None,
    ) -> None:
        self.control_leases = control_leases or _FakeControlLeases([])
        self.work_items = work_items or _FakeWorkItems([])
        self.broker = broker or _FakeBroker([])

    def use_verified_context(self, claims: Any) -> Any:
        return contextlib.nullcontext()


class _FakeApplication:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    def graph_client(self, graph: str) -> Any:
        return self._client


def _session(tenant: str = "tenant-a") -> Any:
    return SimpleNamespace(
        tenant=tenant,
        scopes=frozenset({"kg:read"}),
        actor=SimpleNamespace(authenticated=True),
        engine_verified_context=lambda: {"tenant": tenant},
    )


def _bind(monkeypatch: pytest.MonkeyPatch, client: _FakeClient, *, tenant="tenant-a"):
    monkeypatch.setattr(
        "agent_utilities.api.session.resolve_session",
        lambda *a, **k: _session(tenant=tenant),
    )
    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application",
        lambda: _FakeApplication(client),
    )


class _Req:
    def __init__(self, query: dict[str, str] | None = None):
        self.query_params = query or {}


async def _payload(resp) -> dict[str, Any]:
    import json

    return json.loads(bytes(resp.body).decode("utf-8"))


@pytest.mark.asyncio
async def test_fleet_approvals_lists_active_leases_flattened(monkeypatch):
    lease = {
        "lease_id": "action_approval:1",
        "kind": "action.approval",
        "status": "active",
        "grant": {"kind": "restart_service", "target": "caddy-mcp"},
        "revision": 1,
    }
    client = _FakeClient(control_leases=_FakeControlLeases([lease]))
    _bind(monkeypatch, client)

    resp = await fleet.fleet_approvals(_Req())
    data = await _payload(resp)

    assert data["status"] == "success"
    assert len(data["pending"]) == 1
    assert data["pending"][0]["id"] == "action_approval:1"
    assert data["pending"][0]["status"] == "active"
    assert data["pending"][0]["kind"] == "restart_service"
    assert data["pending"][0]["target"] == "caddy-mcp"


@pytest.mark.asyncio
async def test_fleet_approvals_degrades_to_a_note_on_engine_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr("agent_utilities.api.session.resolve_session", _boom)

    resp = await fleet.fleet_approvals(_Req())
    data = await _payload(resp)

    assert data["pending"] == []
    assert data["note"] == "approval_source_unavailable"


@pytest.mark.asyncio
async def test_fleet_trace_joins_work_items_and_stream_events_by_correlation(
    monkeypatch,
):
    items = [
        {"work_item_id": "wi:1", "metadata": {"correlation_id": "cid-1"}},
        {"work_item_id": "wi:2", "metadata": {"correlation_id": "cid-other"}},
    ]
    matching_event = msgpack.packb(
        {"event_id": "e1", "correlation_id": "cid-1", "subject": "caddy-mcp"}
    )
    other_event = msgpack.packb(
        {"event_id": "e2", "correlation_id": "cid-other", "subject": "vector-mcp"}
    )
    client = _FakeClient(
        work_items=_FakeWorkItems(items),
        broker=_FakeBroker([(0, matching_event), (1, other_event)]),
    )
    _bind(monkeypatch, client)

    resp = await fleet.fleet_trace(_Req({"correlation_id": "cid-1"}))
    data = await _payload(resp)

    assert data["status"] == "success"
    node_ids = {n.get("work_item_id") or n.get("event_id") for n in data["nodes"]}
    assert node_ids == {"wi:1", "e1"}


@pytest.mark.asyncio
async def test_fleet_trace_requires_correlation_id():
    resp = await fleet.fleet_trace(_Req())
    data = await _payload(resp)
    assert resp.status_code == 400
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_fleet_touched_filters_by_subject_newest_first(monkeypatch):
    older = msgpack.packb(
        {
            "event_id": "e1",
            "subject": "caddy-mcp",
            "received_at": "2026-01-01T00:00:00Z",
            "actor_id": "agent:a",
        }
    )
    newer = msgpack.packb(
        {
            "event_id": "e2",
            "subject": "caddy-mcp",
            "received_at": "2026-01-02T00:00:00Z",
            "actor_id": "agent:b",
        }
    )
    unrelated = msgpack.packb({"event_id": "e3", "subject": "vector-mcp"})
    client = _FakeClient(broker=_FakeBroker([(0, older), (1, newer), (2, unrelated)]))
    _bind(monkeypatch, client)

    resp = await fleet.fleet_touched(_Req({"resource": "caddy-mcp"}))
    data = await _payload(resp)

    assert data["status"] == "success"
    assert [e["event_id"] for e in data["events"]] == ["e2", "e1"]
    assert data["actors"] == ["agent:a", "agent:b"]


@pytest.mark.asyncio
async def test_fleet_touched_requires_resource():
    resp = await fleet.fleet_touched(_Req())
    assert resp.status_code == 400
