"""Composition root for GraphOS's served MCP intent surface (GRAPHOS-HOST-R023).

GraphOS serves its eight-tool MCP contract itself: the six intent verbs
(``ask``, ``find``, ``why``, ``write``, ``act``, ``manage``) over its own
operation registry and invocation pipeline, plus the two MCP Apps launcher
tools (task progress, trace waterfall). The agent runtime (agent-utilities)
serves no MCP tools of its own; it only supplies the reusable registrar
function this module calls for the two launcher tools, exactly like any
other library dependency.

``graph_os.mcp_server.server`` calls :func:`register_intent_surface` instead
of the agent runtime's ``agent_utilities.mcp.graphos_surface.register_graphos_surface``
(GRAPHOS-HOST-R024); that cutover, and the agent runtime's MCP-serving removal
(GRAPHOS-HOST-R025), are separate, later changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from graph_os.api.mcp.discovery import FleetSearch
from graph_os.api.mcp.verbs import VERBS, MCPProjection, NLQuery, make_verb
from graph_os.api.registry import Invoke

__all__ = ["register_intent_surface"]


def register_intent_surface(
    mcp: Any,
    *,
    registry: Any,
    services: Any,
    resolver: Any,
    caller_for_request: Callable[[], Any],
    policy_gate: Any,
    invoke: Invoke,
    fleet_search: FleetSearch | None = None,
    nl_query: NLQuery | None = None,
) -> MCPProjection:
    """Register GraphOS's served tools on ``mcp``; return the built projection.

    Registers the six intent verbs over ``registry``/``services``/``invoke``
    (the shared operation chokepoint, GRAPHOS-OPS-R007) and the two MCP Apps
    launcher tools from the agent runtime's reusable registrar. The returned
    :class:`~graph_os.api.mcp.verbs.MCPProjection` lets a caller read
    ``registry.digest`` or build :func:`~graph_os.api.mcp.verbs.mcp_instructions`.
    """

    projection = MCPProjection(
        registry,
        services,
        resolver,
        caller_for_request,
        policy_gate,
        invoke,
        fleet_search,
        nl_query,
    )
    for name in VERBS:
        mcp.add_tool(make_verb(name, projection))
    _register_app_launchers(mcp)
    return projection


def _register_app_launchers(mcp: Any) -> None:
    """The two MCP Apps launcher tools (task progress, trace waterfall).

    Standalone: each launcher tool only returns an opaque id; the app's own
    HTML polls the real data back through the ``ask`` tool this module just
    registered, over the host's own postMessage bridge. No intent-surface
    routing logic lives in the agent runtime's registrar.
    """

    from agent_utilities.mcp.tools.mcp_apps import register_mcp_apps_tools

    register_mcp_apps_tools(mcp)
