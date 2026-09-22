"""Graph-os composition adapters for gateway and supervised co-services."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from agent_utilities.mcp.co_service_supervisor import (
    CoServiceSupervisor,
    detect_composition,
)

from graph_os.gateway.ports import configure_gateway_application
from graph_os.mcp_server import runtime
from graph_os.mcp_server.routes import mount_rest_routes
from graph_os.webui_host import run_web_ui

logger = logging.getLogger(__name__)


class NativeGatewayApplication:
    """Concrete application behavior behind the gateway transport port."""

    async def execute_tool(self, tool: str, /, **kwargs: Any) -> Any:
        return await runtime._execute_tool(tool, **kwargs)

    def engine(self) -> Any:
        return runtime._get_engine()

    def ensure_tools_registered(self) -> None:
        runtime.ensure_tools_registered()

    def mount_rest_routes(self, app: Any, *, prefix: str) -> None:
        mount_rest_routes(app, prefix=prefix)

    def remote_oauth_grant_bindings(self, actor: Any) -> Sequence[Any]:
        from graph_os.fleet.multiplexer import (
            current_remote_oauth_grant_bindings,
        )

        return current_remote_oauth_grant_bindings(actor)


def install_gateway_application() -> NativeGatewayApplication:
    """Install the native application adapter at the process composition root."""

    application = NativeGatewayApplication()
    configure_gateway_application(application)
    return application


def start_composed_services(
    session: Any,
    engine: Any,
    *,
    messaging_intake_enabled: bool | None = None,
) -> CoServiceSupervisor:
    """Start AU agent-plane messaging and the graph-os-owned WebUI host."""

    plan = detect_composition(engine, messaging_intake_enabled=messaging_intake_enabled)
    supervisor = CoServiceSupervisor()
    if plan.messaging_intake_configured:
        from agent_utilities.messaging.daemon import run_forever

        platforms = list(plan.messaging_platforms)

        def run_messaging(stop_event: threading.Event) -> None:
            run_forever(
                engine,
                platforms,
                stop_event,
                session=session,
                intake_intent=True,
            )

        supervisor.start_service("messaging", run_messaging, session)
    elif plan.messaging_configured:
        logger.info("messaging credentials are present but inbound intake is disabled")
    if plan.web_ui_enabled:
        supervisor.start_service("agent-webui", run_web_ui, session)
    return supervisor
