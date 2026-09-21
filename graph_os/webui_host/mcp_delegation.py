"""GraphOS-native MCP helpers injected into the colocated Agent WebUI.

Every operation is submitted asynchronously to the served multiplexer's
owner loop. The WebUI never imports AU's server adapters, opens a second MCP
connection, or keeps a second catalog/probe cache.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from graph_os.fleet.multiplexer import MCPMultiplexer
from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer


class WebUiMcpDelegation(TypedDict):
    """Workspace-helper contract consumed by ``agent_webui``."""

    list_mcp_server_tools: Callable[..., Awaitable[list[dict[str, Any]]]]
    call_mcp_tool: Callable[..., Awaitable[Any]]
    read_mcp_resource: Callable[..., Awaitable[dict[str, str]]]


async def _list_mcp_server_tools(*, server_name: str) -> list[dict[str, Any]]:
    async def list_tools(mux: MCPMultiplexer) -> list[dict[str, Any]]:
        return await mux.delegated_server_tools(server_name)

    return await run_on_served_multiplexer(list_tools)


async def _call_mcp_tool(
    *,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 30.0,
) -> Any:
    async def call_tool(mux: MCPMultiplexer) -> Any:
        return await mux.delegate_server_tool(
            server_name=server_name,
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
