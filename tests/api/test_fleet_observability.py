"""Fleet event reads require caller authority and event tenant evidence."""

from types import SimpleNamespace

import msgpack
import pytest

from graph_os.api.ops import fleet_observability as ops
from graph_os.api.registry import Surface


class Broker:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def stream_read(self, stream, *, from_offset, max):
        self.calls.append((stream, from_offset, max))
        return [row for row in self.rows if row[0] >= from_offset][:max]


def context(rows, *, tenant="tenant-a", service_identity=False):
    return SimpleNamespace(
        caller=SimpleNamespace(tenant=tenant),
        client=SimpleNamespace(broker=Broker(rows)),
        service_identity=service_identity,
    )


def row(offset, **fields):
    return offset, msgpack.packb(fields, use_bin_type=True)


@pytest.mark.asyncio
async def test_trace_filters_event_tenant_and_projects_only_known_fields():
    ctx = context(
        [
            row(
                0,
                tenant_id="tenant-a",
                subject="one",
                correlation_id="c1",
                actor_id="alice",
                secret="hide",
            ),
            row(
                1,
                tenant_id="tenant-b",
                subject="two",
                correlation_id="c1",
                actor_id="bob",
            ),
            row(2, subject="three", correlation_id="c1", actor_id="system"),
            row(3, tenant_id="tenant-a", subject="four", correlation_id="c2"),
        ]
    )
    result = await ops.handle_fleet_observability(
        ctx, {"correlation_id": "c1", "limit": 50}, ops.operations()[0]
    )
    assert result["correlation_id"] == "c1"
    assert result["events"] == [
        {
            "event_id": None,
            "subject": "one",
            "received_at": None,
            "correlation_id": "c1",
            "actor_id": "alice",
            "status": None,
            "severity": None,
            "source_type": None,
        }
    ]
    assert ctx.client.broker.calls[0] == ("fleet.events", 0, 1_000)


@pytest.mark.asyncio
async def test_touched_sorts_bounded_events_and_actors_after_tenant_filter():
    ctx = context(
        [
            row(
                0,
                tenant_id="tenant-a",
                subject="res",
                actor_id="first",
                received_at="2026-01-01",
            ),
            row(
                1,
                tenant_id="tenant-b",
                subject="res",
                actor_id="intruder",
                received_at="2026-12-01",
            ),
            row(
                2,
                tenant_id="tenant-a",
                subject="res",
                actor_id="last",
                received_at="2026-02-01",
            ),
            row(3, tenant_id="tenant-a", subject="other", actor_id="other"),
        ]
    )
    result = await ops.handle_fleet_observability(
        ctx, {"resource": "res", "limit": 1}, ops.operations()[1]
    )
    assert [event["actor_id"] for event in result["events"]] == ["last"]
    assert result["actors"] == ["last"]


@pytest.mark.asyncio
async def test_service_identity_is_refused_before_stream_read():
    ctx = context([], service_identity=True)
    with pytest.raises(PermissionError, match="caller-bound"):
        await ops.handle_fleet_observability(
            ctx, {"resource": "res"}, ops.operations()[1]
        )
    assert ctx.client.broker.calls == []


@pytest.mark.asyncio
async def test_scan_refuses_incomplete_answer(monkeypatch):
    monkeypatch.setattr(ops, "_SCAN_MAX", 2)
    ctx = context(
        [
            row(0, tenant_id="tenant-a", subject="res"),
            row(1, tenant_id="tenant-a", subject="res"),
            row(2, tenant_id="tenant-a", subject="res"),
        ]
    )
    with pytest.raises(RuntimeError, match="exceeded"):
        await ops.handle_fleet_observability(
            ctx, {"resource": "res"}, ops.operations()[1]
        )


def test_ops_are_api_only_and_have_exact_fleet_scope():
    for op in ops.operations():
        assert op.scopes == frozenset({"fleet:read"})
        assert op.surfaces == frozenset({Surface.HTTP, Surface.A2A})
        assert op.executor.value == "caller"
    with pytest.raises(ValueError):
        ops.FleetTraceParams.model_validate(
            {"correlation_id": "c", "tenant": "tenant-b"}
        )
