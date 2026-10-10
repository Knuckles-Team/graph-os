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


async def _assert_ask_denied(
    served_native: SimpleNamespace, actor: ActorContext, session: GraphSession
) -> None:
    """Shared stdio/networked denial assertion: run ``ask`` as ``actor`` and
    confirm the real native-tool entry point denies it without delegating."""
    with use_actor(actor), use_session(session), pytest.raises(ToolError):
        await served_native.call(
            server_name="graph-os", tool_name="ask", arguments={"query": "read"}
        )
    assert served_native.observed == []


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


@pytest.fixture
def native_api(served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    from agent_webui import api_extensions as api

    monkeypatch.setattr(
        api,
        "get_helper",
        lambda name: served_native.call if name == "call_mcp_tool" else None,
    )
    monkeypatch.setattr(api, "get_engine_bounded", AsyncMock(return_value=object()))
    monkeypatch.setattr(api, "_batch_toggle_states", AsyncMock(return_value=({}, True)))
    return api.call_mcp_tool_route


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["tenant-a", "tenant-b"])
async def test_api_native_result_matches_served_mcp(
    served_native: SimpleNamespace, native_api: Any, tenant: str
) -> None:
    session = _session(tenant)
    with use_actor(session.actor), use_session(session):
        native = await served_native.host.call_tool("ask", {"query": "read"})
        result = await native_api(
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
async def test_native_dispatch_denies_admin_only_stdio_caller_without_delegate_scope(
    served_native: SimpleNamespace,
) -> None:
    """GRAPHOS-IDENTITY-R022: the real native-tool entry point
    (``_call_native_tool``, reached here through the served ``graph-os``
    dispatch) must deny a local/stdio caller who holds only a generic
    ``admin`` capability and not the fleet's own ``mcp:delegate`` scope --
    exactly as it would deny an equally-scoped networked caller below."""
    session = _session("tenant-a")
    actor = replace(session.actor, roles=("admin",))
    await _assert_ask_denied(served_native, actor, session)


@pytest.mark.asyncio
async def test_native_dispatch_denies_admin_only_networked_caller_without_delegate_scope(
    served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same real entry point, the networked path: an authenticated HTTP
    bearer caller holding only ``admin`` (never the exact ``mcp:delegate``
    fleet scope) is denied identically to the stdio caller above -- proving
    stdio/networked parity through the production dispatch path rather than
    only the lower-level capability-check unit."""
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request", lambda: SimpleNamespace()
    )
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_access_token",
        lambda: SimpleNamespace(scopes=["admin"], claims=None),
    )
    session = _session("tenant-a")
    await _assert_ask_denied(served_native, session.actor, session)


@pytest.mark.asyncio
async def test_native_dispatch_allows_networked_caller_with_exact_delegate_scope(
    served_native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive parity half: a networked caller holding the exact
    ``mcp:delegate`` fleet scope (no admin grant at all) is allowed through
    the same real entry point, matching the stdio caller's success in
    ``test_api_native_result_matches_served_mcp``."""
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request", lambda: SimpleNamespace()
    )
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_access_token",
        lambda: SimpleNamespace(scopes=["mcp:delegate"], claims=None, client_id=None),
    )
    session = _session("tenant-a")
    with use_actor(session.actor), use_session(session):
        result = await served_native.call(
            server_name="graph-os", tool_name="ask", arguments={"query": "read"}
        )
    assert result["tenant"] == "tenant-a"


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


@pytest.mark.asyncio
async def test_concurrent_native_api_calls_keep_caller_identity(
    served_native: SimpleNamespace, native_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    async def overlapping_read(query: str) -> dict[str, str]:
        before = resolve_session(required_scope="kg:read")
        started.append(before.tenant)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2.0)
        after = resolve_session(required_scope="kg:read")
        return {
            "query": query,
            "tenant": after.tenant,
            "actor": current_actor().actor_id,
        }

    monkeypatch.setitem(runtime.REGISTERED_TOOLS, "ask", overlapping_read)

    async def read_as(tenant: str) -> Any:
        session = _session(tenant)
        with use_actor(session.actor), use_session(session):
            return await native_api(
                {"server": "graph-os", "tool": "ask", "arguments": {"query": tenant}}
            )

    tenants = ("tenant-a", "tenant-b")
    results = await asyncio.wait_for(
        asyncio.gather(*(read_as(tenant) for tenant in tenants)), timeout=5.0
    )
    assert sorted(started) == list(tenants)
    for tenant, response in zip(tenants, results, strict=True):
        assert response == {
            "status": "success",
            "result": {"query": tenant, "tenant": tenant, "actor": f"user-{tenant}"},
        }
    served_native.mux.delegate_server_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_server_inventory_lists_registered_native_tools(
    served_native: SimpleNamespace,
) -> None:
    """GRAPHOS-HOST-R017: ``graph-os`` is native, not a catalog child probe."""
    served_native.mux.delegated_server_tools = AsyncMock(
        side_effect=AssertionError("native server must not be probed as a child")
    )
    list_tools = webui_mcp_delegation_helpers()["list_mcp_server_tools"]
    session = _session("tenant-a")
    with use_actor(session.actor), use_session(session):
        tools = await list_tools(server_name="graph-os")
    assert [tool["name"] for tool in tools] == ["ask"]
    assert tools[0]["inputSchema"]["properties"] == {"query": {"type": "string"}}
