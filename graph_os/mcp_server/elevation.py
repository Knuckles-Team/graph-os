"""Just-in-time RBAC elevation for agents and chat (EH-405).

``graph_elevation`` (MCP) and its action-routed REST twin ``POST
/graph/elevation`` let an agent -- including one driven from chat -- REQUEST
a time-boxed elevation, LIST the elevations it is a party to, and REVOKE one.
There is deliberately no ``approve`` action: an approval is a human
decision taken from the operator console (``POST /elevations/approve``,
:mod:`graph_os.gateway.elevation`), never by a tool an agent can call.

Authority is the served surface's usual one: the verified tool session and the
tenant's session-routed EG client under ``use_verified_context``. The request
carries no identity; epistemic-graph stamps the actor from the verified
context, decides, audits and expires (EH-404).
"""

from __future__ import annotations

from typing import Any, Literal

from agent_utilities.security.elevation import (
    MAX_SPAN_MS,
    ElevationRequest,
    ElevationRevocation,
    ElevationScope,
    ElevationService,
)
from pydantic import BaseModel, ConfigDict, Field

from graph_os.mcp_server import runtime

__all__ = ["ElevationToolRequest", "register_elevation_tools"]

TOOL_NAME = "graph_elevation"


class ElevationToolRequest(BaseModel):
    """One agent-side elevation operation; the fields each action needs."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["request", "list", "revoke"]
    scopes: list[ElevationScope] = Field(default_factory=list, max_length=16)
    span_ms: int = Field(default=0, ge=0, le=MAX_SPAN_MS)
    justification: str = Field(default="", max_length=2048)
    elevation_id: str = Field(default="", max_length=128)


async def _dispatch(service: ElevationService, request: ElevationToolRequest) -> Any:
    if request.action == "list":
        return [view.model_dump() for view in await service.list_elevations()]
    if request.action == "revoke":
        revocation = ElevationRevocation(elevation_id=request.elevation_id)
        return (await service.revoke(revocation)).model_dump()
    ask = ElevationRequest(
        scopes=request.scopes,
        span_ms=request.span_ms,
        justification=request.justification,
        elevation_id=request.elevation_id or None,
    )
    return (await service.request(ask)).model_dump()


async def handle_elevation(session: Any, request: ElevationToolRequest) -> Any:
    """Run one operation as the verified ``session`` (shared with A2A)."""
    client = runtime.graph_client(str(session.tenant))
    claims = session.engine_verified_context()
    with client.use_verified_context(claims):
        service = ElevationService(client, caller=str(claims["agent_id"]))
        return await _dispatch(service, request)


_DESCRIPTION = (
    "Request, list or revoke a time-boxed access elevation for the calling "
    "identity. Actions: request (scopes: [{graph, action: read|write}], "
    "span_ms, justification), list, revoke (elevation_id). A request grants "
    "nothing until a different person approves it from the operator console; "
    "it expires on its own."
)


async def graph_elevation(request: ElevationToolRequest) -> Any:
    """The agent elevation tool: runs as the verified tool session."""
    with runtime.verified_tool_session_scope() as session:
        return await handle_elevation(session, request)


def register_elevation_tools(mcp: Any) -> None:
    """Register the agent elevation tool and its REST twin."""
    tags = {"graph-os", "security", "elevation"}
    mcp.tool(name=TOOL_NAME, description=_DESCRIPTION, tags=tags)(graph_elevation)
    runtime.REGISTERED_TOOLS[TOOL_NAME] = graph_elevation
