"""A child effect is resolved before invoke allows dispatch."""

from __future__ import annotations

import hashlib
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import pytest

from graph_os.api.errors import FleetRefusal
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
    _child_error_code,
    annotation_effect,
    compose_fleet_gateway,
    fleet_effect_for,
    oauth_delegated_call_for_mux,
    tool_for_multiplexer_ops,
)
from graph_os.fleet.service_child import ServiceChildOutcomeUnknown


def _gateway_and_caller(
    *,
    tool_for: object,
    policy: object,
    delegate: object,
    principal: str | None = "alice",
) -> tuple[FleetGateway, SimpleNamespace]:
    gateway = FleetGateway(
        tool_for=tool_for,  # type: ignore[arg-type]
        policy_check=policy,  # type: ignore[arg-type]
        delegated_call=delegate,  # type: ignore[arg-type]
    )
    fields: dict[str, object] = {
        "effective_scopes": frozenset({"mcp:delegate"}),
        "session": object(),
    }
    if principal is not None:
        fields["principal"] = principal
    return gateway, SimpleNamespace(**fields)


def test_child_refusal_uses_structured_code_only() -> None:
    assert (
        _child_error_code(
            SimpleNamespace(structured_content={"error": {"code": "CHILD_BUSY"}})
        )
        == "CHILD_BUSY"
    )
    assert (
        _child_error_code(
            SimpleNamespace(structured_content={"error": {"code": "secret value"}})
        )
        == "CHILD_REFUSED"
    )
    assert (
        _child_error_code(
            SimpleNamespace(content=[{"type": "text", "text": "CHILD_BUSY"}])
        )
        == "CHILD_REFUSED"
    )


@pytest.mark.asyncio
async def test_delegated_child_refusal_keeps_bounded_structured_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ModuleType("agent_utilities.api")
    api.use_session = lambda _session: nullcontext()  # type: ignore[attr-defined]
    brain = ModuleType("agent_utilities.security.brain_context")
    brain.use_actor = lambda _actor: nullcontext()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_utilities.api", api)
    monkeypatch.setitem(sys.modules, "agent_utilities.security.brain_context", brain)

    class Mux:
        async def call_oauth_gated_tool(self, *_args: object) -> object:
            return SimpleNamespace(
                is_error=True,
                structured_content={
                    "error": {"code": "CHILD_BUSY", "message": "secret"}
                },
            )

    actor = SimpleNamespace(authenticated=True, actor_id="alice", tenant_id="acme")
    caller = SimpleNamespace(
        session=SimpleNamespace(actor=actor), principal="alice", tenant="acme"
    )
    with pytest.raises(FleetRefusal) as refused:
        await oauth_delegated_call_for_mux(Mux())("search", "query", {}, caller)
    assert (refused.value.code, refused.value.server, refused.value.tool) == (
        "CHILD_BUSY",
        "search",
        "query",
    )
    assert "secret" not in str(refused.value)


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

    gateway, caller = _gateway_and_caller(
        tool_for=tool_for, policy=policy, delegate=delegate
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

    gateway, caller = _gateway_and_caller(
        tool_for=tool_for, policy=policy, delegate=delegate
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

    gateway, caller = _gateway_and_caller(
        tool_for=tool_for, policy=policy, delegate=delegate, principal=None
    )
    with pytest.raises(RuntimeError, match="effect changed"):
        await gateway.call(caller, "s", "t", {}, expected_effect=Effect.WRITE)


def test_fleet_ops_are_distinct_and_loading_is_mcp_only() -> None:
    registry = Registry(fleet.specs())
    assert {spec.id for spec in registry} == {
        "fleet.call",
        "fleet.catalog.search",
        "fleet.catalog.list",
        "fleet.tools.load",
        "fleet.tools.unload",
        "fleet.status",
    }
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
async def test_uncertain_service_child_propagates_for_the_invoke_surface_to_map() -> (
    None
):
    """``handle_fleet_call`` does not itself translate an unconfirmed service-
    child outcome into a typed invoke-pipeline refusal: that mapping belongs
    to the served invocation surface, which does not exist on this repository
    yet (GRAPHOS-FLEET-R012's remaining note). Until it does, the recovery
    reference must still reach the caller intact on the raised exception."""

    class Gateway:
        async def call(self, *_args: object, **_kwargs: object) -> object:
            raise ServiceChildOutcomeUnknown(
                "child response lost", recovery_ref="recovery:42"
            )

    context = SimpleNamespace(
        caller=object(),
        services={"fleet_gateway": Gateway()},
        service_identity=True,
        owner="alice",
        owner_ref="principal:sha256:" + hashlib.sha256(b"alice").hexdigest(),
        fleet_decision=object(),
        registry_digest="a" * 64,
    )
    operation = next(op for op in fleet.specs() if op.id == "fleet.call")
    with pytest.raises(ServiceChildOutcomeUnknown) as failed:
        await fleet.handle_fleet_call(
            context, {"server": "s", "tool": "run", "arguments": {}}, operation
        )
    assert failed.value.recovery_ref == "recovery:42"


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
        _server: str,
        _tool: str,
        _args: object,
        _caller: object,
        owner_ref: str,
        registry_digest: str,
    ) -> str:
        stamped.append(owner_ref)
        assert registry_digest == "a" * 64
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
            registry_digest="a" * 64,
        )
        == "ok"
    )
    assert stamped == [owner_ref]


def test_gateway_composition_requires_all_authorities() -> None:
    with pytest.raises(ValueError, match="authorities are required"):
        compose_fleet_gateway(ops=None, mux=object(), policy_check=lambda *_: True)
