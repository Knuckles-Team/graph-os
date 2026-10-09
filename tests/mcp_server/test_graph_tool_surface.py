"""graph-os serves the agent-utilities intent contract and adds its own operations."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.mcp_server import runtime


def test_surface_serves_intent_contract_and_registers_host_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.mcp import graphos_surface, kg_server

    from graph_os.a2a import mcp as a2a_mcp
    from graph_os.browser_control import mcp as browser_mcp

    served = object()
    backing = object()
    calls: list[tuple[str, Any, Any]] = []

    def surface(mcp: Any, **_: Any) -> Any:
        calls.append(("surface", mcp, None))
        kg_server.REGISTERED_TOOLS["graph_query"] = object()
        return backing

    monkeypatch.setattr(graphos_surface, "register_graphos_surface", surface)
    monkeypatch.setattr(
        browser_mcp,
        "register_browser_control_tools",
        lambda target: calls.append(("browser", target, None)),
    )
    monkeypatch.setattr(
        a2a_mcp,
        "register_a2a_tools",
        lambda mcp, *, tools=None: calls.append(("a2a", mcp, tools)),
    )
    monkeypatch.setattr(runtime, "REGISTERED_TOOLS", {})
    monkeypatch.setattr(kg_server, "REGISTERED_TOOLS", {})

    runtime._register_graph_tool_surface(served)

    assert calls == [
        ("surface", served, None),
        # Browser control and A2A are ``act`` operations on the backing server.
        ("browser", backing, None),
        ("a2a", served, backing),
    ]
    # REST dispatches exactly what the intent tools route to.
    assert "graph_query" in runtime.REGISTERED_TOOLS
