"""Native WebUI calls reuse served MCP middleware and verified caller context."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agent_utilities.api.session import GraphSession, resolve_session, use_session
from agent_utilities.mcp.middlewares import ActorContextMiddleware
from agent_utilities.security.brain_context import (
    ActorContext,
    current_actor,
    use_actor,
)
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from graph_os.fleet.multiplexer import _MAX_DELEGATED_VALUE_BYTES
from graph_os.fleet.shared_multiplexer import _reset_served_multiplexer_for_tests
from graph_os.mcp_server import runtime
from graph_os.webui_host.mcp_delegation import webui_mcp_delegation_helpers
from tests.fleet.test_shared_multiplexer_loop import _LoopOwner


def _session(tenant: str, *, scopes: tuple[str, ...] = ("kg:read",)) -> GraphSession:
    actor = ActorContext(
        actor_id=f"user-{tenant}",
        roles=("mcp:delegate",),
        tenant_id=tenant,
        authenticated=True,
    )
    return GraphSession(
        actor=actor,
        tenant=tenant,
        scopes=frozenset(scopes),
        graph=tenant,
        policy_version="policy-1",
        audience="epistemic-graph",
    )


@pytest.fixture
def served_native(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    _reset_served_multiplexer_for_tests()
    observed: list[str] = []

    async def implementation(query: str) -> dict[str, str]:
        session = resolve_session(required_scope="kg:read")
        observed.append(session.tenant)
        return {
            "query": query,
            "tenant": session.tenant,
            "actor": current_actor().actor_id,
        }

    async def ask(query: str) -> Any:
        return await runtime._execute_tool("ask", query=query)

    monkeypatch.setitem(runtime.REGISTERED_TOOLS, "ask", implementation)
    host = FastMCP("graph-os")
    host.add_middleware(ActorContextMiddleware(require_verified_session=True))
    host.tool(ask, name="ask")
    host.tool(implementation, name="unregistered_handler")
    mux = SimpleNamespace(
        _host_mcp=host, delegate_server_tool=AsyncMock(return_value={"child": True})
    )
    owner = _LoopOwner(mux)
    owner.start()
    try:
        yield SimpleNamespace(
            host=host,
            mux=mux,
            observed=observed,
            call=webui_mcp_delegation_helpers()["call_mcp_tool"],
        )
    finally:
        owner.close()
        _reset_served_multiplexer_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["tenant-a", "tenant-b"])
async def test_api_native_result_matches_served_mcp(
    served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tenant: str
) -> None:
    from agent_webui import api_extensions as api

    monkeypatch.setattr(
        api,
        "get_helper",
        lambda name: served_native.call if name == "call_mcp_tool" else None,
    )
    monkeypatch.setattr(api, "get_engine_bounded", AsyncMock(return_value=object()))
    monkeypatch.setattr(api, "_batch_toggle_states", AsyncMock(return_value=({}, True)))
    session = _session(tenant)
    with use_actor(session.actor), use_session(session):
        native = await served_native.host.call_tool("ask", {"query": "read"})
        result = await api.call_mcp_tool_route(
            {
                "server": "graph-os",
                "tool": "ask",
                "arguments": {"query": "read"},
                "_meta": {"x-session-id": "another-tenant"},
            }
        )
    assert result == {"status": "success", "result": native.structured_content}
    assert result["result"]["tenant"] == tenant
    served_native.mux.delegate_server_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_child_route_is_unchanged(served_native: SimpleNamespace) -> None:
    result = await served_native.call(
        server_name="docs", tool_name="search", arguments={"q": "read"}, timeout=4.0
    )
    assert result == {"child": True}
    served_native.mux.delegate_server_tool.assert_awaited_once_with(
        server_name="docs", tool_name="search", arguments={"q": "read"}, timeout=4.0
    )
    assert served_native.observed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["unknown", "unregistered_handler"])
async def test_unknown_native_handler_is_not_exposed(
    served_native: SimpleNamespace, tool: str
) -> None:
    session = _session("tenant-a")
    with use_actor(session.actor), use_session(session), pytest.raises(ToolError):
        await served_native.call(server_name="graph-os", tool_name=tool, arguments={})
    assert served_native.observed == []
    served_native.mux.delegate_server_tool.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial", ["scope", "tenant", "identity", "session", "delegate"]
)
async def test_native_authority_denial_prevents_execution(
    served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    session = _session("tenant-a", scopes=() if denial == "scope" else ("kg:read",))
    actor = _session("tenant-b").actor if denial == "tenant" else session.actor
    if denial == "delegate":
        actor = replace(session.actor, roles=())
        session = replace(session, actor=actor)
    if denial == "session":
        monkeypatch.setattr(runtime, "_PROCESS_SESSION", session)
    if denial == "identity":
        actor = ActorContext(
            actor_id="anonymous", tenant_id="tenant-a", authenticated=False
        )
    session_scope = use_session(session) if denial != "session" else nullcontext()
    with (
        use_actor(actor),
        session_scope,
        pytest.raises((ToolError, PermissionError)),
    ):
        await served_native.call(
            server_name="graph-os", tool_name="ask", arguments={"query": "read"}
        )
    assert served_native.observed == []


@pytest.mark.asyncio
async def test_api_disabled_native_tool_keeps_policy_denial(
    served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_webui import api_extensions as api
    from fastapi import HTTPException

    monkeypatch.setattr(api, "get_helper", lambda name: served_native.call)
    monkeypatch.setattr(api, "get_engine_bounded", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        api,
        "_batch_toggle_states",
        AsyncMock(return_value=({"graph-os:ask": False}, True)),
    )
    session = _session("tenant-a")
    with (
        use_actor(session.actor),
        use_session(session),
        pytest.raises(HTTPException) as raised,
    ):
        await api.call_mcp_tool_route(
            {"server": "graph-os", "tool": "ask", "arguments": {"query": "read"}}
        )
    assert raised.value.status_code == 403
    assert served_native.observed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["size", "timeout"])
async def test_native_delegation_retains_bounds(
    served_native: SimpleNamespace, boundary: str
) -> None:
    session = _session("tenant-a")
    arguments = (
        {"query": "x" * (_MAX_DELEGATED_VALUE_BYTES + 1)}
        if boundary == "size"
        else {"query": "read"}
    )
    timeout = 30.0 if boundary == "size" else 0.0
    with (
        use_actor(session.actor),
        use_session(session),
        pytest.raises((ToolError, asyncio.TimeoutError)),
    ):
        await served_native.call(
            server_name="graph-os",
            tool_name="ask",
            arguments=arguments,
            timeout=timeout,
        )
    assert served_native.observed == []
