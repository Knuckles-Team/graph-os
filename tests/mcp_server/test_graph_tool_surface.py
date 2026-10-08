"""graph-os mounts the agent-utilities ``graph_*`` tool surface it serves."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.mcp_server import runtime


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []


@pytest.mark.parametrize(
    ("mode", "intent"), [("intent", True), ("hybrid", True), ("both", False)]
)
def test_surface_mounts_condensed_verbose_and_intent_by_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str, intent: bool
) -> None:
    from agent_utilities.mcp import kg_server, tools, verbose_tools
    from agent_utilities.mcp.tools import intent_tools

    seen = _Recorder()

    def surface(mcp: Any, **kwargs: Any) -> list[str]:
        seen.calls.append(("surface", kwargs))
        kg_server.REGISTERED_TOOLS["graph_query"] = object()
        return []

    monkeypatch.setattr(verbose_tools, "register_tool_surface", surface)
    monkeypatch.setattr(verbose_tools, "tool_mode", lambda: mode)
    monkeypatch.setattr(
        tools, "register_mcp_apps_tools", lambda m: seen.calls.append(("apps", None))
    )
    monkeypatch.setattr(
        intent_tools,
        "register_intent_tools",
        lambda m: seen.calls.append(("intent", None)),
    )
    monkeypatch.setattr(runtime, "REGISTERED_TOOLS", {})
    monkeypatch.setattr(kg_server, "REGISTERED_TOOLS", {})

    runtime._register_graph_tool_surface(object())

    kinds = [kind for kind, _ in seen.calls]
    assert kinds[:2] == ["surface", "apps"]
    assert ("intent" in kinds) is intent
    surface_kwargs = seen.calls[0][1]
    assert surface_kwargs["service"] == "graph-os"
    assert (
        surface_kwargs["verbose_register"] is kg_server.register_graphos_verbose_tools
    )
    assert len(surface_kwargs["registrars"]) == len(runtime._graph_tool_registrars())
    # REST and the multiplexer dispatch exactly what MCP registered.
    assert "graph_query" in runtime.REGISTERED_TOOLS
