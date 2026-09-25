"""The served control plane refuses incomplete or widened MCP composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool

from graph_os.api.mcp.verbs import VERBS, MCPProjection
from graph_os.mcp_server import runtime


def _tool(name: str) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        fn=lambda: None,
    )


def test_served_api_requires_all_authorities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_API_PROJECTION", None)
    monkeypatch.setattr(runtime, "_API_VISIBILITY", None)
    monkeypatch.setattr(runtime, "_FLEET_OPS_FACTORY", None)
    with pytest.raises(RuntimeError, match="not bound"):
        runtime.served_api()


def test_served_api_rejects_divergent_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_API_PROJECTION", None)
    registry = object()
    projection = MCPProjection(
        registry,
        SimpleNamespace(registry=object()),
        object(),
        lambda: None,
        lambda *_: False,
    )
    with pytest.raises(ValueError, match="identical"):
        runtime.configure_served_api(projection, lambda *_: False, lambda *_: None)


def test_resident_surface_is_exact_ten() -> None:
    mcp = FastMCP("cutover")
    for name in (
        *VERBS,
        "find_tools",
        "load_tools",
        "unload_tools",
        "multiplexer_status",
    ):
        mcp.add_tool(_tool(name))
    runtime.verify_resident_tools(mcp)
    mcp.add_tool(_tool("graph_elevation"))
    with pytest.raises(RuntimeError, match="extra"):
        runtime.verify_resident_tools(mcp)
    mcp.local_provider.remove_tool("graph_elevation")
    mcp.local_provider.remove_tool("ask")
    with pytest.raises(RuntimeError, match="missing"):
        runtime.verify_resident_tools(mcp)
