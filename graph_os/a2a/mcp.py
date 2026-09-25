"""Protocol-native A2A routes for the GraphOS MCP host."""

from __future__ import annotations

from typing import Any

from .application import AmbientA2AAuthenticator, create_a2a_handlers
from .composition import compose_a2a_service, hosted_control_plane
from .service import A2AService

_SERVICE: A2AService | None = None


def _service() -> A2AService:
    global _SERVICE
    if _SERVICE is None:
        from graph_os.mcp_server import runtime

        _SERVICE = compose_a2a_service(
            control_plane_for=hosted_control_plane(runtime.graph_client),
        )
    return _SERVICE


def register_a2a_protocol_routes(mcp: Any) -> None:
    """Keep native Agent Card and JSON-RPC routes, without a granular tool."""
    card_handler, rpc_handler = create_a2a_handlers(
        service=_service(), authenticator=AmbientA2AAuthenticator()
    )
    mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])(card_handler)
    mcp.custom_route("/a2a", methods=["POST"])(rpc_handler)


__all__ = ["register_a2a_protocol_routes"]
