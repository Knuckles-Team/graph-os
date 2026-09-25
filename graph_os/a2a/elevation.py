"""Just-in-time RBAC elevation over A2A (EH-405).

A2A peers are agents, so they get the agent surface: ``elevation/request``,
``elevation/list`` and ``elevation/revoke`` JSON-RPC methods backed by the
same service as the ``graph_elevation`` MCP tool. ``elevation/approve`` is
answered with a typed refusal -- an approval is a person's decision in the
operator console, and a peer can never make it on anyone's behalf.
"""

from __future__ import annotations

from typing import Any

from agent_utilities.api.security import ElevationRefused
from pydantic import BaseModel, ConfigDict

from .models import A2ASkill

__all__ = [
    "ELEVATION_METHODS",
    "ELEVATION_SKILL",
    "ELEVATION_WRITE_METHODS",
    "A2AElevationResult",
    "invoke_elevation",
]

_PREFIX = "elevation/"
ELEVATION_WRITE_METHODS = frozenset({"elevation/request", "elevation/revoke"})
ELEVATION_METHODS = ELEVATION_WRITE_METHODS | {"elevation/list", "elevation/approve"}

ELEVATION_SKILL = A2ASkill(
    id="graph-os-elevation",
    name="Just-in-time access elevation",
    description=(
        "Request, list or revoke a time-boxed access elevation for the calling "
        "identity (elevation/request, elevation/list, elevation/revoke). A "
        "person approves it in the operator console; it is never approved "
        "over A2A."
    ),
    tags=["security", "access"],
    input_modes=["application/json"],
    output_modes=["application/json"],
)


class A2AElevationResult(BaseModel):
    """The elevations one method returned (one for request/revoke)."""

    model_config = ConfigDict(extra="forbid")

    elevations: list[dict[str, Any]]


async def invoke_elevation(method: str, raw: dict[str, Any]) -> A2AElevationResult:
    """Run one ``elevation/*`` method as the request's verified session."""
    if method == "elevation/approve":
        raise ElevationRefused(
            "ELEVATION_APPROVAL_SURFACE", "elevations are not approved over A2A"
        )
    from agent_utilities.api.session import resolve_session

    from graph_os.mcp_server.elevation import ElevationToolRequest, handle_elevation

    request = ElevationToolRequest.model_validate(
        {**raw, "action": method.removeprefix(_PREFIX)}
    )
    result = await handle_elevation(resolve_session(), request)
    return A2AElevationResult(
        elevations=result if isinstance(result, list) else [result]
    )
