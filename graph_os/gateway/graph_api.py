"""Gateway composition for the GraphOS operation API and native protocols."""

from __future__ import annotations

from typing import Any


def register_graph_routes(app: Any, prefix: str = "/api") -> None:
    """Mount the shared operation API and the remaining protocol routes.

    ``prefix`` is retained for the gateway calling convention. The public API
    has one fixed versioned path, independent of the retired ``/api/graph``
    namespace. Missing operation or policy bindings stop composition.
    """
    from agent_utilities.core.config import config
    from agent_utilities.security.request_identity import ActorIdentityMiddleware

    from graph_os.api.http.app import create_api_application
    from graph_os.gateway.ports import gateway_application
    from graph_os.mcp_server.runtime import served_api

    application = gateway_application()
    application.ensure_tools_registered()
    projection, visibility = served_api()
    if config.gateway_rate_limit > 0:
        from graph_os.gateway.rate_limit import GatewayRateLimitMiddleware

        app.add_middleware(GatewayRateLimitMiddleware)
    app.add_middleware(ActorIdentityMiddleware)
    if config.gateway_metrics:
        from agent_utilities.observability.gateway_metrics import (
            GatewayMetricsMiddleware,
            metrics_asgi_endpoint,
        )

        app.add_middleware(GatewayMetricsMiddleware)
        if not any(getattr(route, "path", None) == "/metrics" for route in app.routes):
            app.add_route("/metrics", metrics_asgi_endpoint, methods=["GET"])

    from graph_os.gateway.fleet_events import fleet_events_receive
    from graph_os.gateway.remote_oauth_api import register_remote_oauth_routes

    app.add_route("/fleet/events", fleet_events_receive, methods=["POST"])
    register_remote_oauth_routes(app, prefix="")
    app.mount(
        "/api/v1",
        create_api_application(services=projection.services, visibility=visibility),
    )
