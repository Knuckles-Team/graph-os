"""REST projection for the clean-cut GraphOS action manifest."""

from __future__ import annotations

from typing import Any

from graph_os.mcp_server import runtime


def _route(app: Any, prefix: str, path: str, handler: Any) -> None:
    app.add_route(prefix + path, handler, methods=["POST"])


def mount_rest_routes(app: Any, prefix: str = "") -> None:
    """Mount exactly the actions registered by native GraphOS composition.

    Legacy AU knowledge-graph/MCP actions are intentionally absent. Generated
    EG/SDK adapters and the typed AU RLM operation extend the manifest only
    when their verified composition adapters are installed.
    """

    for tool_name, path in runtime.ACTION_TOOL_ROUTES.items():
        _route(app, prefix, path, runtime._make_tool_endpoint(tool_name))


__all__ = ["mount_rest_routes"]
