"""A child effect is resolved before invoke allows dispatch."""

from __future__ import annotations

import hashlib
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import pytest

from graph_os.api.ops import fleet
from graph_os.api.registry import (
    Confirm,
    Effect,
    Executor,
    PrincipalRule,
    Registry,
    Surface,
)
from graph_os.fleet.gateway_ops import (
    AdmittedTool,
    FleetGateway,
    annotation_effect,
    compose_fleet_gateway,
    fleet_effect_for,
    oauth_delegated_call_for_mux,
    tool_for_multiplexer_ops,
)


def test_annotations_default_to_write_and_destructive_wins() -> None:
    assert annotation_effect(None) == (Effect.WRITE, Confirm.NONE, PrincipalRule.ANY)
    assert annotation_effect({"readOnlyHint": True})[0] is Effect.READ
    assert annotation_effect({"readOnlyHint": True, "destructiveHint": True}) == (
        Effect.DESTRUCTIVE,
        Confirm.PLAN,
        PrincipalRule.ANY,
    )


def test_manifest_admin_override_requires_console_human() -> None:
    assert annotation_effect({"readOnlyHint": True}, override="admin") == (
        Effect.ADMIN,
        Confirm.CONSOLE,
        PrincipalRule.HUMAN_UNDELEGATED,
    )
    with pytest.raises(ValueError, match="invalid fleet effect"):
        annotation_effect(None, override="unknown")


@pytest.mark.asyncio
async def test_effect_hook_uses_caller_filtered_descriptor() -> None:
    calls: list[tuple[str, str, object]] = []

    async def descriptor(server: str, tool: str, caller: object):
        calls.append((server, tool, caller))
        return SimpleNamespace(destructive_hint=True), None

    caller = object()
    resolve = fleet_effect_for(descriptor)
    assert await resolve(None, {"server": "s", "tool": "t"}, caller) == (
        Effect.DESTRUCTIVE,
        Confirm.PLAN,
        PrincipalRule.ANY,
    )
    assert calls == [("s", "t", caller)]


@pytest.mark.asyncio
async def test_gateway_rechecks_child_scope_and_policy_before_delegation() -> None:
    dispatched: list[str] = []

    async def tool_for(_server: str, _tool: str, _caller: object) -> AdmittedTool:
        return AdmittedTool(None, None, frozenset({"finance:read"}), "delegated")

    async def policy(_server: str, _tool: str, _caller: object) -> bool:
        return True

    async def delegate(_server: str, _tool: str, _args: object, _caller: object) -> str:
        dispatched.append("called")
        return "ok"

    gateway = FleetGateway(
        tool_for=tool_for, policy_check=policy, delegated_call=delegate
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate"}),
        session=object(),
        principal="alice",
    )
    with pytest.raises(PermissionError, match="child scopes"):
        await gateway.call(caller, "s", "t", {}, expected_effect=Effect.WRITE)
    assert dispatched == []
    caller.effective_scopes = frozenset({"mcp:delegate", "finance:read"})
    assert (
        await gateway.call(caller, "s", "t", {}, expected_effect=Effect.WRITE) == "ok"
    )
    assert dispatched == ["called"]


@pytest.mark.asyncio
async def test_service_credential_child_fails_closed() -> None:
    async def tool_for(_server: str, _tool: str, _caller: object) -> AdmittedTool:
        return AdmittedTool(None, "admin", frozenset(), "service")

    async def policy(_server: str, _tool: str, _caller: object) -> bool:
        return True

    async def delegate(_server: str, _tool: str, _args: object, _caller: object) -> str:
        pytest.fail("service credential child reached delegated dispatcher")

    gateway = FleetGateway(
        tool_for=tool_for, policy_check=policy, delegated_call=delegate
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate"}),
        session=object(),
        principal="alice",
    )
    with pytest.raises(PermissionError, match="authority changed"):
        await gateway.call(caller, "s", "t", {}, expected_effect=Effect.ADMIN)


@pytest.mark.asyncio
async def test_effect_change_after_preview_refuses_dispatch() -> None:
    async def tool_for(_server: str, _tool: str, _caller: object) -> AdmittedTool:
        return AdmittedTool({"destructiveHint": True}, None, frozenset(), "delegated")

    async def policy(_server: str, _tool: str, _caller: object) -> bool:
        return True

    async def delegate(_server: str, _tool: str, _args: object, _caller: object) -> str:
        pytest.fail("effect change reached child dispatcher")

    gateway = FleetGateway(
        tool_for=tool_for, policy_check=policy, delegated_call=delegate
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate"}), session=object()
    )
    with pytest.raises(RuntimeError, match="effect changed"):
        await gateway.call(caller, "s", "t", {}, expected_effect=Effect.WRITE)


def test_fleet_ops_are_distinct_and_loading_is_mcp_only() -> None:
    registry = Registry(fleet.specs())
    assert len(registry) == 6
    assert registry["fleet.catalog.search"].scopes == frozenset({"mcp:discover"})
    assert registry["fleet.tools.load"].surfaces == frozenset({Surface.MCP})
    assert registry["fleet.call"].scopes == frozenset({"mcp:delegate"})


@pytest.mark.asyncio
async def test_fleet_catalog_binding_requires_exact_scope() -> None:
    class Ops:
        async def search(self, caller: object, **params: object) -> dict[str, object]:
            return {"items": [], "next_cursor": None}

    async def tool_for(_server: str, _tool: str, _caller: object) -> AdmittedTool:
        raise AssertionError("unexpected child lookup")

    async def policy(_server: str, _tool: str, _caller: object) -> bool:
        raise AssertionError("unexpected policy lookup")

    async def delegate(_server: str, _tool: str, _args: object, _caller: object) -> str:
        raise AssertionError("unexpected child call")

    gateway = FleetGateway(
        tool_for=tool_for,
        policy_check=policy,
        delegated_call=delegate,
        catalog_ops=Ops(),
    )
    caller = SimpleNamespace(effective_scopes=frozenset(), session=object())
    with pytest.raises(PermissionError, match="mcp:discover"):
        await gateway.search(caller, query="x")
    caller.effective_scopes = frozenset({"mcp:discover"})
    assert await gateway.search(caller, query="x") == {"items": [], "next_cursor": None}
    caller.session = None
    assert await gateway.search(caller, query="x") == {"items": [], "next_cursor": None}
    caller.effective_scopes = frozenset({"mcp:delegate"})
    with pytest.raises(PermissionError, match="verified fleet session"):
        await gateway.load(caller, items=("s/t",))


@pytest.mark.asyncio
async def test_fleet_operation_handler_uses_only_bound_gateway() -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    class Gateway:
        async def search(self, caller: object, **params: object) -> dict[str, object]:
            calls.append((caller, params))
            return {"items": [], "next_cursor": None}

    caller = object()
    context = SimpleNamespace(caller=caller, services={"fleet_gateway": Gateway()})
    op = next(op for op in fleet.specs() if op.id == "fleet.catalog.search")
    assert await fleet.handle_fleet_operation(context, {"query": "test"}, op) == {
        "value": {"items": [], "next_cursor": None}
    }
    assert calls == [(caller, {"query": "test"})]
    with pytest.raises(RuntimeError, match="not bound"):
        await fleet.handle_fleet_operation(
            SimpleNamespace(caller=caller, services={}), {"query": "test"}, op
        )


@pytest.mark.asyncio
async def test_oauth_callback_uses_matching_verified_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_utilities.api
    import agent_utilities.security.brain_context as brain_context

    bound: list[object] = []
    monkeypatch.setattr(
        agent_utilities.api,
        "use_session",
        lambda session: (bound.append(session), nullcontext())[1],
    )
    monkeypatch.setattr(
        brain_context,
        "use_actor",
        lambda actor: (bound.append(actor), nullcontext())[1],
    )

    class Mux:
        async def call_oauth_gated_tool(self, server: str, tool: str, arguments: dict):
            assert (server, tool, arguments) == ("s", "t", {"x": 1})
            return SimpleNamespace(
                is_error=False,
                model_dump=lambda **_kwargs: {
                    "content": [{"type": "text", "text": "ok"}]
                },
            )

    actor = SimpleNamespace(authenticated=True, actor_id="alice", tenant_id="acme")
    session = SimpleNamespace(actor=actor)
    caller = SimpleNamespace(session=session, principal="alice", tenant="acme")
    dispatch = oauth_delegated_call_for_mux(Mux())
    assert await dispatch("s", "t", {"x": 1}, caller) == {
        "content": [{"type": "text", "text": "ok"}]
    }
    assert bound == [actor, session]
    caller.principal = "bob"
    with pytest.raises(PermissionError, match="does not match"):
        await dispatch("s", "t", {}, caller)


@pytest.mark.asyncio
async def test_admitted_tool_adapter_rejects_target_or_metadata_drift() -> None:
    class Ops:
        def __init__(self) -> None:
            self.item = SimpleNamespace(
                kind="tool",
                server="s",
                name="t",
                annotations={"readOnlyHint": True},
                effect_override=None,
                required_scopes=frozenset({"finance:read"}),
                credential_mode="delegated",
            )

        async def admitted_tool(self, caller: object, server: str, tool: str):
            return self.item

    ops = Ops()
    tool_for = tool_for_multiplexer_ops(ops)
    caller = object()
    assert await tool_for("s", "t", caller) == AdmittedTool(
        {"readOnlyHint": True}, None, frozenset({"finance:read"}), "delegated"
    )
    ops.item.name = "other"
    with pytest.raises(PermissionError, match="not admitted"):
        await tool_for("s", "t", caller)
    ops.item.name = "t"
    ops.item.credential_mode = "unknown"
    with pytest.raises(RuntimeError, match="metadata is incomplete"):
        await tool_for("s", "t", caller)


@pytest.mark.asyncio
async def test_service_child_requires_resolved_decision_and_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True)
    class Decision:
        effect: Effect
        confirm: Confirm
        principals: PrincipalRule
        executor: Executor
        required_scopes: frozenset[str]
        executor_scopes: frozenset[str]
        subject_id: str | None
        credential_mode: str

    invoke_module = ModuleType("graph_os.api.invoke")
    invoke_module.FleetCallDecision = Decision  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.invoke", invoke_module)
    descriptor = AdmittedTool(
        None,
        "admin",
        frozenset({"finance:read"}),
        "service",
        frozenset({"fleet:events"}),
        "component:server:1",
    )

    async def tool_for(_server: str, _tool: str, _caller: object) -> AdmittedTool:
        return descriptor

    async def policy(_server: str, _tool: str, _caller: object) -> bool:
        return True

    async def delegated(*_args: object) -> object:
        pytest.fail("service tool reached delegated path")

    stamped: list[str] = []

    async def service(
        _server: str, _tool: str, _args: object, _caller: object, owner_ref: str
    ) -> str:
        stamped.append(owner_ref)
        return "ok"

    gateway = FleetGateway(
        tool_for=tool_for,
        policy_check=policy,
        delegated_call=delegated,
        service_call=service,
    )
    caller = SimpleNamespace(
        effective_scopes=frozenset({"mcp:delegate", "finance:read"}),
        session=object(),
        principal="alice",
    )
    decision = await gateway.effect(None, {"server": "s", "tool": "t"}, caller)
    assert decision.executor is Executor.SERVICE
    owner_ref = "principal:sha256:" + hashlib.sha256(b"alice").hexdigest()
    with pytest.raises(PermissionError, match="authority changed"):
        await gateway.call(
            caller,
            "s",
            "t",
            {},
            expected_effect=Effect.ADMIN,
            service_identity=False,
            owner="alice",
            owner_ref=owner_ref,
            fleet_decision=decision,
        )
    with pytest.raises(PermissionError, match="authority changed"):
        await gateway.call(
            caller,
            "s",
            "t",
            {},
            expected_effect=Effect.ADMIN,
            service_identity=True,
            owner="alice",
            owner_ref="principal:sha256:" + "0" * 64,
            fleet_decision=decision,
        )
    assert (
        await gateway.call(
            caller,
            "s",
            "t",
            {},
            expected_effect=Effect.ADMIN,
            service_identity=True,
            owner="alice",
            owner_ref=owner_ref,
            fleet_decision=decision,
        )
        == "ok"
    )
    assert stamped == [owner_ref]


def test_gateway_composition_requires_all_authorities() -> None:
    with pytest.raises(ValueError, match="authorities are required"):
        compose_fleet_gateway(ops=None, mux=object(), policy_check=lambda *_: True)
