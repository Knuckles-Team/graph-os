"""Prepared FastMCP registration exercised with explicit synthetic authorities."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from graph_os.api.invoke import OpError, OpResult, VerifiedCaller

from graph_os.api.mcp.registration import (
    RESIDENT_NAMES,
    FleetMCPBinding,
    GovernedSessionVisibility,
    register_mcp_tools,
)
from graph_os.api.mcp.verbs import MCPProjection
from graph_os.api.ops.fleet import operations
from graph_os.api.policy import PolicyGate
from graph_os.api.registry import Registry, Surface
from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog
from graph_os.fleet.multiplexer import _make_forwarder, make_governed_tool_mount
from graph_os.fleet.multiplexer_ops import MultiplexerOps
from graph_os.fleet.session_loads import SessionLoads


@pytest.fixture
def surface() -> Any:
    scopes = frozenset({"mcp:discover", "mcp:delegate", "data:read"})
    caller = VerifiedCaller(
        principal="alice",
        tenant="tenant",
        effective_scopes=scopes,
        engine_claims={
            "principal": "alice",
            "tenant": "tenant",
            "scopes": scopes,
            "policy_version": "p1",
            "delegated": False,
        },
        principal_kind="human",
        authenticated=True,
        delegated=False,
        credential_kind="session",
        policy_revision="p1",
        session="session-a",
    )
    state = SimpleNamespace(
        caller=caller,
        allowed=True,
        outcome=None,
        calls=[],
        notifications=[],
        mcp=FastMCP("prepared"),
        mux=SimpleNamespace(),
        services=object(),
    )
    rows = (
        CatalogItem(
            "fleet:tool:s/read",
            "tool",
            "read",
            server="s",
            required_scopes=frozenset({"data:read"}),
            schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "additionalProperties": False,
            },
        ),
        CatalogItem("fleet:skill:s/help", "skill", "help", server="s", body="help"),
    )

    async def source():
        return rows

    async def allowed(_item, _caller):
        return state.allowed

    async def notify(key):
        state.notifications.append(key)
        return True

    async def invoke(op, params, current, surface, *, services, **kwargs):
        assert services is state.services
        assert surface == Surface.MCP
        state.calls.append((op, dict(params), current))
        if state.outcome is not None:
            return state.outcome
        routes = {
            "fleet.catalog.search": state.ops.search,
            "fleet.tools.load": state.ops.load,
            "fleet.tools.unload": state.ops.unload,
            "fleet.status": state.ops.status,
        }
        if op in routes:
            return OpResult(await routes[op](current, **params))
        assert op == "fleet.call"
        return OpResult({"child": "called"})

    async def bound_invoke(op, params, current, surface):
        return await invoke(op, params, current, surface, services=state.services)

    def native_name(_item):
        return "s_read"

    state.ops = MultiplexerOps(
        catalog=FleetCatalog([source], allowed),
        sessions=SessionLoads(),
        loadable=allowed,
        callable_item=allowed,
        mount=make_governed_tool_mount(state.mcp, state.mux, native_name),
        notify=notify,
        invoke=bound_invoke,
        health=lambda: {},
        session_key_for=lambda current: current.session,
        native_name=native_name,
    )
    state.projection = MCPProjection(
        registry=Registry(operations()),
        services=state.services,
        resolver=SimpleNamespace(scope_ref=lambda *args, **kwargs: "scope"),
        caller_for_request=lambda: state.caller,
        policy_gate=PolicyGate(mode="none"),
        invoke=invoke,
    )
    state.binding = FleetMCPBinding(
        state.projection, state.ops, lambda current: current.session
    )
    state.tool_id = rows[0].id
    state.native_name = "s_read"
    return state


def test_exactly_ten_residents_and_browse_replaces_list_catalog(surface) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        tools = await surface.mcp.list_tools()
        assert {tool.name for tool in tools} == set(RESIDENT_NAMES)
        assert len(tools) == 10
        assert "list_catalog" not in {tool.name for tool in tools}
        tool = next(tool for tool in tools if tool.name == "find_tools")
        result = await tool.run({"browse": True})
        assert {
            row["kind"] for row in result.structured_content["result"]["items"]
        } == {"tool", "skill"}
        assert surface.calls[0][0:2] == ("fleet.catalog.search", {"browse": True})
        with pytest.raises(RuntimeError, match="empty and unbound"):
            await register_mcp_tools(surface.mcp, surface.mux, surface.binding)

    asyncio.run(run())


def test_load_native_and_intent_share_invoke_and_refresh_caller(surface) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        native = await surface.mcp.get_tool(surface.native_name)
        result = await native.run({"x": 3})
        assert result.structured_content["result"] == {"child": "called"}
        assert surface.calls[-1][0:2] == (
            "fleet.call",
            {"server": "s", "tool": "read", "arguments": {"x": 3}},
        )
        surface.caller = replace(surface.caller, request_id="fresh")
        act = await surface.mcp.get_tool("act")
        await act.run({"op": "fleet.call", "params": surface.calls[-1][1]})
        assert surface.calls[-1][2].request_id == "fresh"
        surface.caller = replace(surface.caller, session="session-b")
        with pytest.raises(ToolError, match="UNKNOWN_TOOL"):
            await native.run({"x": 3})
        assert len(surface.calls) == 2

    asyncio.run(run())


def test_load_and_call_reauthorize_and_retract_revoked_tool(surface) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        surface.allowed = False
        with pytest.raises(PermissionError):
            await surface.ops.load(surface.caller, items=[surface.tool_id])
        assert not surface.ops.sessions.loaded("session-a")
        surface.allowed = True
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        surface.outcome = OpError("POLICY_DENIED")
        native = await surface.mcp.get_tool(surface.native_name)
        with pytest.raises(ToolError, match="POLICY_DENIED"):
            await native.run({})
        assert not surface.ops.dispatchable("session-a", surface.native_name)
        assert surface.notifications == ["session-a"] * 3
        assert len(surface.calls) == 1

    asyncio.run(run())


def test_scope_revocation_filters_list_and_prevents_stale_native_call(surface) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        surface.caller = replace(surface.caller, effective_scopes=frozenset())
        middleware = GovernedSessionVisibility(surface.binding)

        async def listed(_context):
            return [
                SimpleNamespace(name=name)
                for name in (*RESIDENT_NAMES, surface.native_name)
            ]

        visible = await middleware.on_list_tools(None, listed)
        assert {tool.name for tool in visible} == set(RESIDENT_NAMES)
        with pytest.raises(ToolError, match="UNKNOWN_TOOL"):
            await _make_forwarder(surface.mux, surface.native_name)()
        assert surface.calls == []

    asyncio.run(run())


def test_missing_governed_binding_never_dispatches_child() -> None:
    async def run():
        async def forbidden(*_args):
            pytest.fail("direct child dispatch bypass")

        mux = SimpleNamespace(call_proxied_tool=forbidden)
        with pytest.raises(ToolError, match="governed_fleet_unavailable"):
            await _make_forwarder(mux, "child")()

    asyncio.run(run())


def test_incomplete_attachment_fails_before_binding(surface, monkeypatch) -> None:
    add = surface.mcp.add_tool
    monkeypatch.setattr(
        surface.mcp, "add_tool", lambda tool: add(tool) if tool.name != "act" else None
    )
    with pytest.raises(RuntimeError, match="attachment was incomplete"):
        asyncio.run(register_mcp_tools(surface.mcp, surface.mux, surface.binding))
    assert not hasattr(surface.mux, "_governed_fleet")


def test_governed_native_preserves_child_annotations_and_meta(surface) -> None:
    from mcp.types import Annotations, CallToolResult, TextContent

    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        child = CallToolResult(
            content=[
                TextContent(
                    type="text", text="result", annotations=Annotations(priority=0.8)
                )
            ],
            structuredContent={"answer": 42},
            _meta={"child_trace": "trace"},
        )
        surface.outcome = OpResult(
            {"value": child.model_dump(mode="json", by_alias=True)}
        )
        result = await _make_forwarder(surface.mux, surface.native_name)()
        assert result.content[0].annotations.priority == 0.8
        assert result.structured_content == {"answer": 42}
        assert result.meta == {"child_trace": "trace"}
        assert surface.calls[-1][0] == "fleet.call"

    asyncio.run(run())


def test_native_idle_expiry_and_one_shot_never_bypass_membership(surface) -> None:
    async def run():
        clock = [0.0]
        surface.ops.sessions = SessionLoads(clock=lambda: clock[0])
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        await surface.ops.load(
            surface.caller, items=[surface.tool_id], auto_unload=True
        )
        forward = _make_forwarder(surface.mux, surface.native_name)
        await forward()
        with pytest.raises(ToolError, match="UNKNOWN_TOOL"):
            await forward()
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        clock[0] = 3600.0
        with pytest.raises(ToolError, match="UNKNOWN_TOOL"):
            await forward()
        assert len(surface.calls) == 1

    asyncio.run(run())


def test_all_four_residents_use_the_shared_services(surface) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        for name, params in (
            ("find_tools", {"browse": True}),
            ("load_tools", {"items": [surface.tool_id]}),
            ("multiplexer_status", {}),
            ("unload_tools", {"all_items": True}),
        ):
            tool = await surface.mcp.get_tool(name)
            result = await tool.run(params)
            assert result.structured_content["ok"] is True
        assert [call[0] for call in surface.calls] == [
            "fleet.catalog.search",
            "fleet.tools.load",
            "fleet.status",
            "fleet.tools.unload",
        ]
        assert not surface.ops.sessions.loaded("session-a")

    asyncio.run(run())


def test_current_unauthenticated_caller_cannot_use_previously_loaded_tool(
    surface,
) -> None:
    async def run():
        await register_mcp_tools(surface.mcp, surface.mux, surface.binding)
        await surface.ops.load(surface.caller, items=[surface.tool_id])
        surface.caller = None
        with pytest.raises(ToolError, match="UNAUTHENTICATED"):
            await _make_forwarder(surface.mux, surface.native_name)()
        assert surface.calls == []

    asyncio.run(run())
