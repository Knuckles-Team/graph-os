"""GraphOS-native MCP helpers injected into the colocated Agent WebUI.

Every operation is submitted asynchronously to the served multiplexer's
owner loop. The WebUI never imports AU's server adapters, opens a second MCP
connection, or keeps a second catalog/probe cache.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, TypedDict

from fastmcp.exceptions import ToolError

from graph_os.fleet.multiplexer import (
    MCPMultiplexer,
    _assert_bounded_delegated_value,
    _child_result_payload,
    _provider_tools,
    _require_fleet_capability,
)
from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer


class WebUiMcpDelegation(TypedDict):
    """Workspace-helper contract consumed by ``agent_webui``."""

    list_mcp_server_tools: Callable[..., Awaitable[list[dict[str, Any]]]]
    call_mcp_tool: Callable[..., Awaitable[Any]]
    read_mcp_resource: Callable[..., Awaitable[dict[str, str]]]


#: The served host's own server name. It is native, never a catalog child.
NATIVE_SERVER_NAME = "graph-os"


async def _native_server_tools(mux: MCPMultiplexer) -> list[dict[str, Any]]:
    """List the registered native tools that ``_call_native_tool`` admits."""
    from graph_os.mcp_server import runtime

    _require_fleet_capability("discover")
    host = mux._host_mcp
    if host is None:
        raise ToolError("Native GraphOS tool surface is unavailable")
    return [
        {
            "name": name,
            "description": getattr(tool, "description", "") or "",
            "inputSchema": getattr(tool, "parameters", None) or {},
        }
        for name, tool in sorted(_provider_tools(host).items())
        if name in runtime.REGISTERED_TOOLS
    ]


async def _child_server_tools(
    server_name: str, mux: MCPMultiplexer
) -> list[dict[str, Any]]:
    return await mux.delegated_server_tools(server_name)


async def _list_mcp_server_tools(*, server_name: str) -> list[dict[str, Any]]:
    async def list_tools(mux: MCPMultiplexer) -> list[dict[str, Any]]:
        native = {NATIVE_SERVER_NAME: _native_server_tools}
        lister = native.get(server_name, partial(_child_server_tools, server_name))
        return await lister(mux)

    return await run_on_served_multiplexer(list_tools)


async def _call_native_tool(
    mux: MCPMultiplexer,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float,
) -> Any:
    """Invoke only registered native tools through the served middleware chain."""
    from agent_utilities.api.session import current_session

    from graph_os.mcp_server import runtime

    _require_fleet_capability("delegate")
    _assert_bounded_delegated_value(arguments)
    if current_session() is None:
        raise ToolError("Verified caller session required")
    host = mux._host_mcp
    if host is None or tool_name not in runtime.REGISTERED_TOOLS:
        raise ToolError("Native GraphOS tool is unavailable")
    with runtime.verified_tool_session_scope():
        result = await asyncio.wait_for(
            host.call_tool(tool_name, arguments, run_middleware=True), timeout=timeout
        )
    return _child_result_payload(result)


async def _call_mcp_tool(
    *,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 30.0,
) -> Any:
    async def call_tool(mux: MCPMultiplexer) -> Any:
        native = {NATIVE_SERVER_NAME: partial(_call_native_tool, mux)}
        dispatch = native.get(
            server_name, partial(mux.delegate_server_tool, server_name=server_name)
        )
        return await dispatch(
            tool_name=tool_name,
            arguments=arguments,
            timeout=timeout,
        )

    return await run_on_served_multiplexer(call_tool)


async def _read_mcp_resource(
    *, server_name: str, uri: str, timeout: float = 30.0
) -> dict[str, str]:
    async def read_resource(mux: MCPMultiplexer) -> dict[str, str]:
        return await mux.read_server_resource(
            server_name=server_name, uri=uri, timeout=timeout
        )

    return await run_on_served_multiplexer(read_resource)


def webui_mcp_delegation_helpers() -> WebUiMcpDelegation:
    """Return the three governed helpers backed by the served authority."""
    return WebUiMcpDelegation(
        list_mcp_server_tools=_list_mcp_server_tools,
        call_mcp_tool=_call_mcp_tool,
        read_mcp_resource=_read_mcp_resource,
    )
