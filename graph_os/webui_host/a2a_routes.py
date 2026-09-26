"""Expose the native GraphOS A2A handlers on the supervised WebUI listener.

The MCP listener and WebUI listener use the same :class:`A2AService` and its
AU WorkItem authority. This module only adds another HTTP projection; it does
not create a broker, task store, or independent A2A execution path.
"""

from __future__ import annotations

from typing import Any

from graph_os.a2a.application import AmbientA2AAuthenticator, create_a2a_handlers
from graph_os.a2a.service import A2AService

_ROUTES = (
    ("/.well-known/agent-card.json", "GET"),
    ("/a2a", "POST"),
)


def register_a2a_routes(app: Any, *, service: A2AService | None = None) -> None:
    """Mount the one native A2A service on WebUI before its SPA catch-all.

    Refuse route collisions so a future WebUI dependency cannot silently
    replace the authenticated task authority with a different implementation.
    """
    if not callable(getattr(app, "add_api_route", None)):
        raise TypeError("the WebUI A2A host requires a FastAPI application")
    for route in getattr(app, "routes", ()):
        route_path = getattr(route, "path", None)
        methods = getattr(route, "methods", ()) or ()
        if any(route_path == path and method in methods for path, method in _ROUTES):
            raise RuntimeError(f"WebUI A2A route already registered: {route_path}")

    # The MCP and WebUI listeners share the exact service and operation
    # projection; otherwise the WebUI Agent Card would omit native operations
    # and graphos.plan/confirm would be unavailable on this listener.
    from graph_os.a2a.mcp import _operation_projection, _service

    if service is None:
        service = _service()
    card_handler, rpc_handler = create_a2a_handlers(
        service=service,
        authenticator=AmbientA2AAuthenticator(),
        operation_projection=_operation_projection(),
    )
    app.add_api_route(_ROUTES[0][0], card_handler, methods=[_ROUTES[0][1]])
    app.add_api_route(_ROUTES[1][0], rpc_handler, methods=[_ROUTES[1][1]])
