"""Explicit application ports consumed by the graph-os REST gateway.

The gateway owns HTTP and host composition.  Tool execution, engine access,
fleet discovery, and the canonical action routes are supplied by the graph-os
MCP/fleet composition (G2); they are not imported from agent-utilities
implementations by transport modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "GatewayApplicationPort",
    "configure_gateway_application",
    "gateway_application",
]


@runtime_checkable
class GatewayApplicationPort(Protocol):
    """Application behavior composed behind the REST transport boundary."""

    async def execute_tool(self, tool: str, /, **kwargs: Any) -> Any:
        """Execute one canonical graph-os action and return decoded data."""

    def engine(self) -> Any:
        """Return the process-scoped engine adapter used by host-only routes."""

    def ensure_tools_registered(self) -> None:
        """Make the canonical action catalog available before routes are mounted."""

    def mount_rest_routes(self, app: Any, *, prefix: str) -> None:
        """Mount the canonical action-routed REST surface."""

    def remote_oauth_grant_bindings(self, actor: Any) -> Sequence[Any]:
        """Return current fleet OAuth bindings from the fleet authority."""


_application: GatewayApplicationPort | None = None


def configure_gateway_application(application: GatewayApplicationPort) -> None:
    """Install the application adapter once at the graph-os composition root."""

    global _application
    if not isinstance(application, GatewayApplicationPort):
        raise TypeError("application does not implement GatewayApplicationPort")
    _application = application


def gateway_application() -> GatewayApplicationPort:
    """Return the configured adapter, failing closed before composition."""

    if _application is None:
        raise RuntimeError(
            "graph-os gateway application port is not configured; "
            "the G2 composition root must install it before serving requests"
        )
    return _application
