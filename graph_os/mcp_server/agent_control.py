"""GraphOS transport adapter for AU's typed application control plane."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from agent_utilities.api.agent_control_plane import (
    AgentControlPlane,
    GraphRlmRequest,
    compose_agent_control_plane,
)

from graph_os.mcp_server import runtime

ControlPlaneFactory = Callable[[Any, Any], AgentControlPlane]
ClientForSession = Callable[[Any], Any]


class ToolRegistrar(Protocol):
    def tool(self, **kwargs: Any) -> Callable[[Any], Any]: ...


def register_graph_rlm(
    mcp: ToolRegistrar,
    *,
    client_for_session: ClientForSession,
    factory: ControlPlaneFactory = compose_agent_control_plane,
) -> None:
    """Register the sole retained AU operation from verified injected inputs."""

    @mcp.tool(
        name="graph_rlm",
        description=(
            "Run, benchmark, or evolve a recursive language model through the "
            "verified agent control plane."
        ),
        tags={"graph-os", "agent-control-plane", "rlm"},
    )
    async def graph_rlm(request: GraphRlmRequest) -> str:
        with runtime.verified_tool_session_scope() as session:
            control_plane = factory(client_for_session(session), session)
            result = await control_plane.graph_rlm(request)
        return result.model_dump_json()

    runtime.REGISTERED_TOOLS["graph_rlm"] = graph_rlm
    runtime.ACTION_TOOL_ROUTES["graph_rlm"] = "/graph/rlm"


__all__ = ["register_graph_rlm"]
