"""Protocol-native A2A routes for the GraphOS MCP host."""

from __future__ import annotations

from typing import Any

from .application import AmbientA2AAuthenticator, create_a2a_handlers
from .composition import compose_a2a_service, hosted_control_plane
from .op_invoke import OperationProjection
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


def _operation_projection() -> OperationProjection:
    """Use the MCP host's verified caller, registry and policy for A2A."""
    from graph_os.api.policy import op_resource
    from graph_os.api.registry import Surface
    from graph_os.mcp_server import runtime

    mcp_projection, _visibility = runtime.served_api()

    async def card_discovery(caller: Any) -> tuple[str, ...]:
        candidates = tuple(
            op for op in mcp_projection.registry if Surface.A2A in op.surfaces
        )
        decisions = await mcp_projection.policy_gate.visible(
            [op_resource(op) for op in candidates], caller
        )
        if len(decisions) != len(candidates):
            raise ValueError("A2A policy response alignment failed")
        return tuple(
            op.id
            for op, allowed in zip(candidates, decisions, strict=True)
            if allowed is True
        )

    return OperationProjection(
        mcp_projection.services,
        caller=mcp_projection.caller_for_request,
        card_discovery=card_discovery,
    )


def register_a2a_protocol_routes(mcp: Any) -> None:
    """Keep native Agent Card and JSON-RPC routes, without a granular tool."""
    card_handler, rpc_handler = create_a2a_handlers(
        service=_service(),
        authenticator=AmbientA2AAuthenticator(),
        operation_projection=_operation_projection(),
    )
    mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])(card_handler)
    mcp.custom_route("/a2a", methods=["POST"])(rpc_handler)


__all__ = ["register_a2a_protocol_routes"]
