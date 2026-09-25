"""A child effect is resolved before invoke allows dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.registry import Confirm, Effect, PrincipalRule
from graph_os.fleet.gateway_ops import (
    AdmittedTool,
    FleetGateway,
    annotation_effect,
    fleet_effect_for,
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
    caller = SimpleNamespace(effective_scopes=frozenset({"mcp:delegate"}))
    with pytest.raises(PermissionError, match="child scopes"):
        await gateway.call(caller, "s", "t", {})
    assert dispatched == []
    caller.effective_scopes = frozenset({"mcp:delegate", "finance:read"})
    assert await gateway.call(caller, "s", "t", {}) == "ok"
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
    caller = SimpleNamespace(effective_scopes=frozenset({"mcp:delegate"}))
    with pytest.raises(PermissionError, match="SERVICE binding"):
        await gateway.call(caller, "s", "t", {})
