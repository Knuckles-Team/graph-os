"""Fixed supported variants and verified tenant boundaries for DecisionLog."""

from types import SimpleNamespace

import pytest

pytest.importorskip("graph_os.api.registry")

from graph_os.api.ops import decisions  # noqa: E402
from graph_os.api.registry import Composite  # noqa: E402


def test_only_supported_read_scoped_composite_ops_are_declared() -> None:
    ops = decisions.specs()
    assert {op.id for op in ops} == {"decisions.get", "decisions.aggregate"}
    for op in ops:
        assert op.scopes == frozenset({"agent:decision-read"})
        assert isinstance(op.binding, Composite)


@pytest.mark.asyncio
async def test_handlers_fix_variant_and_verified_tenant(monkeypatch) -> None:
    context = SimpleNamespace(caller=SimpleNamespace(tenant="verified-tenant"))
    calls = []

    async def capture(_context, request):
        calls.append(request)
        return {"ok": True}

    monkeypatch.setattr(decisions, "_read", capture)
    await decisions.get_handler(context, {"record_id": "r1"}, None)
    await decisions.aggregate_handler(
        context,
        {"question_id": "assemble", "window": {"from_ms": 0, "to_ms": 10}},
        None,
    )
    assert [request["op"] for request in calls] == ["get", "aggregate"]
    assert calls[0]["tenant_id"] == "verified-tenant"
    assert calls[1]["request"] == {
        "tenant_id": "verified-tenant",
        "question_id": "assemble",
        "window": {"from_ms": 0, "to_ms": 10},
    }


@pytest.mark.asyncio
async def test_caller_cannot_supply_tenant_or_variant() -> None:
    context = SimpleNamespace(caller=SimpleNamespace(tenant="verified-tenant"))
    with pytest.raises(ValueError):
        await decisions.get_handler(
            context, {"record_id": "r1", "tenant_id": "other"}, None
        )
    with pytest.raises(ValueError):
        await decisions.aggregate_handler(
            context,
            {"window": {"from_ms": 0, "to_ms": 10}, "attribution": {}},
            None,
        )
    with pytest.raises(ValueError):
        await decisions.aggregate_handler(
            context,
            {"window": {"from_ms": 0, "to_ms": 10}, "tenant_id": "other"},
            None,
        )
    with pytest.raises(ValueError):
        await decisions.aggregate_handler(
            context,
            {"window": {"from_ms": 10, "to_ms": 0}},
            None,
        )


@pytest.mark.asyncio
async def test_read_uses_scoped_engine_transport(monkeypatch) -> None:
    from epistemic_graph.generated import coordination

    transport = object()
    context = SimpleNamespace(client=SimpleNamespace(_client=transport))
    seen = {}

    async def send(client, params):
        seen.update(client=client, params=params)
        return SimpleNamespace(payload={"record_id": "r1"})

    monkeypatch.setattr(coordination, "send_decision_log", send)
    result = await decisions._read(context, {"op": "get", "tenant_id": "t"})
    assert seen == {
        "client": transport,
        "params": {"op": {"op": "get", "tenant_id": "t"}},
    }
    assert result == {"record_id": "r1"}
