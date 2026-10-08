"""Session bounds and native forwarding contract for the prepared MCP surface."""

from __future__ import annotations

import pytest

from graph_os.fleet.session_loads import LoadCapExceeded, SessionLoads


def test_default_cap_requires_explicit_lru_and_preserves_other_sessions() -> None:
    clock = [0.0]
    sessions = SessionLoads(clock=lambda: clock[0])
    original = [f"tool-{index}" for index in range(64)]
    sessions.load("first", original)
    sessions.load("second", ["private"])
    with pytest.raises(LoadCapExceeded):
        sessions.load("first", ["overflow"])
    assert sessions.loaded("first") == frozenset(original)
    clock[0] = 1.0
    assert sessions.touch("first", "tool-0")
    result = sessions.load("first", ["overflow"], evict="lru")
    assert result["evicted"] == ["tool-1"]
    assert "tool-0" in sessions.loaded("first")
    assert len(sessions.loaded("first")) == 64
    assert sessions.loaded("second") == frozenset({"private"})


def test_hard_cap_and_one_hour_idle_expiry() -> None:
    with pytest.raises(ValueError, match="1..256"):
        SessionLoads(cap=257)
    clock = [0.0]
    sessions = SessionLoads(cap=256, clock=lambda: clock[0])
    sessions.load("first", [f"tool-{index}" for index in range(256)])
    with pytest.raises(LoadCapExceeded):
        sessions.load("first", ["overflow"])
    clock[0] = 3599.0
    assert sessions.active_keys() == ("first",)
    clock[0] = 3600.0
    assert sessions.active_keys() == ()
    assert sessions.loaded("first") == frozenset()


def test_gateway_effect_returns_only_canonical_authoritative_decisions() -> None:
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace

    from graph_os.api.invoke import FleetCallDecision

    from graph_os.api.registry import Effect, Executor
    from graph_os.fleet.gateway_ops import AdmittedTool, FleetGateway

    descriptor = AdmittedTool(
        {"readOnlyHint": True}, None, frozenset({"data:read"}), "delegated"
    )
    current = [descriptor]
    policy = [True]

    async def tool_for(*_args):
        return current[0]

    async def allowed(*_args):
        return policy[0]

    async def forbidden(*_args):
        pytest.fail("classification must never dispatch a child")

    gateway = FleetGateway(
        tool_for=tool_for, policy_check=allowed, delegated_call=forbidden
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate", "data:read"}), session=object()
    )

    async def run():
        decision = await gateway.effect(None, {"server": "s", "tool": "read"}, caller)
        assert isinstance(decision, FleetCallDecision)
        assert decision.effect is Effect.READ
        assert decision.executor is Executor.CALLER
        assert decision.required_scopes == descriptor.required_scopes
        assert decision.executor_scopes == frozenset()
        assert decision.subject_id is None
        assert decision.credential_mode == "delegated"
        policy[0] = False
        with pytest.raises(PermissionError, match="denied by policy"):
            await gateway.effect(None, {"server": "s", "tool": "read"}, caller)
        policy[0] = True
        current[0] = replace(descriptor, credential_mode="unknown")
        with pytest.raises(RuntimeError, match="authority metadata"):
            await gateway.effect(None, {"server": "s", "tool": "read"}, caller)
        current[0] = replace(descriptor, executor_scopes=frozenset({"service:read"}))
        with pytest.raises(RuntimeError, match="cannot carry service"):
            await gateway.effect(None, {"server": "s", "tool": "read"}, caller)
        current[0] = replace(descriptor, credential_mode="service")
        with pytest.raises(RuntimeError, match="authority metadata"):
            await gateway.effect(None, {"server": "s", "tool": "read"}, caller)

    asyncio.run(run())


def test_delegated_catalog_subject_is_not_service_executor_authority() -> None:
    import asyncio
    from types import SimpleNamespace

    from graph_os.fleet.catalog_items import CatalogItem
    from graph_os.fleet.catalog_sources import (
        _merge_probed_item,
        _verified_server_policy,
    )
    from graph_os.fleet.gateway_ops import FleetGateway, tool_for_multiplexer_ops

    item = CatalogItem(
        "fleet:tool:s/read",
        "tool",
        "read",
        server="s",
        annotations={"readOnlyHint": True},
    )
    policy = _verified_server_policy(
        b'{"credential_mode":"delegated","required_scopes":["data:read"]}'
    )
    merged: dict[str, CatalogItem] = {}
    _merge_probed_item(
        merged, item, policy=policy, subject_id="verified-server-component"
    )

    async def admitted(*_args):
        return merged[item.id]

    async def allowed(*_args):
        return True

    async def forbidden(*_args):
        pytest.fail("classification must not dispatch")

    adapter = tool_for_multiplexer_ops(SimpleNamespace(admitted_tool=admitted))
    gateway = FleetGateway(
        tool_for=adapter, policy_check=allowed, delegated_call=forbidden
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate", "data:read"}), session=object()
    )

    async def run():
        descriptor = await adapter("s", "read", caller)
        decision = await gateway.effect(None, {"server": "s", "tool": "read"}, caller)
        assert merged[item.id].subject_id == "verified-server-component"
        assert descriptor.subject_id is None
        assert decision.subject_id is None
        assert decision.credential_mode == "delegated"
        assert decision.required_scopes == frozenset({"data:read"})

    asyncio.run(run())


@pytest.mark.parametrize("change", ["unload", "reload", "evict", "expire"])
def test_inflight_grant_is_invalidated_by_session_owner(change: str) -> None:
    clock = [0.0]
    sessions = SessionLoads(cap=1, clock=lambda: clock[0])
    sessions.load("s", ["a"], bindings={"a": ("tool", "server", "original")})
    grant = sessions.acquire("s", "a")
    assert grant is not None
    if change == "unload":
        sessions.unload("s", ["a"])
    elif change == "reload":
        sessions.load("s", ["a"], bindings={"a": ("tool", "server", "original")})
    elif change == "evict":
        sessions.load("s", ["b"], evict="lru")
    else:
        clock[0] = 3600.0
    assert not sessions.current(grant)
    assert not sessions.dispatch(grant)
    sessions.release(grant)
    if change == "reload":
        assert sessions.loaded("s") == {"a"}


def test_incomplete_binding_cannot_partially_evict_a_session() -> None:
    sessions = SessionLoads(cap=1)
    sessions.load("s", ["a"])
    with pytest.raises(ValueError, match="binding is incomplete"):
        sessions.load("s", ["b"], evict="lru", bindings={})
    assert sessions.loaded("s") == {"a"}
