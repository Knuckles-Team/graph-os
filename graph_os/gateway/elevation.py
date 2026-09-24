"""The operator console's elevation approval (EH-405).

``POST /elevations/approve`` is the ONLY place GraphOS approves a
just-in-time RBAC elevation. It is a plain gateway route, not an MCP tool, so
no agent (chat, fleet child or A2A peer) can reach it through a tool call.
Requesting, listing and revoking use the shared ``graph_elevation`` action
route (:mod:`graph_os.mcp_server.elevation`).

The approver is the caller's verified request session -- never a body field.
The body names only WHICH request (``elevation_id``) and the digest of the
request the approver was shown (``request_digest``). The shared AU client
refuses a delegated session, a session without the exact
``rbac:approve-elevation`` scope, the approver's own request and a stale view
before anything is sent; epistemic-graph then applies its own two-person,
exact-digest and one-shot rules and audits the transition.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_utilities.security.elevation import (
    ElevationApproval,
    ElevationRefused,
    ElevationService,
    ElevationSurface,
)
from agent_utilities.security.error_surface import public_error_payload
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["elevation_approve", "mount_elevation_routes"]

_NO_STORE = {"Cache-Control": "no-store"}


def _refusal(code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "code": code}, status_code=status, headers=_NO_STORE
    )


async def _approval(request: Request) -> ElevationApproval | None:
    try:
        return ElevationApproval.model_validate(await request.json())
    except (ValueError, ValidationError):
        return None


async def _approve(approval: ElevationApproval) -> dict[str, Any]:
    from agent_utilities.api.session import resolve_session

    from graph_os.gateway.ports import gateway_application

    session = resolve_session(required_scope="kg:read")
    claims = session.engine_verified_context()
    client = gateway_application().graph_client(session.tenant)
    with client.use_verified_context(claims):
        service = ElevationService(client, caller=str(claims["agent_id"]))
        view = await service.approve(
            approval, surface=ElevationSurface.OPERATOR_CONSOLE, claims=claims
        )
    return view.model_dump()


async def elevation_approve(request: Request) -> JSONResponse:
    """Approve one exact elevation request as the signed-in operator."""
    approval = await _approval(request)
    if approval is None:
        return _refusal("invalid_request", 400)
    try:
        elevation = await _approve(approval)
    except ElevationRefused as refused:
        return _refusal(refused.code, 403)
    except PermissionError:
        return _refusal("ELEVATION_ACCESS_DENIED", 403)
    except Exception as exc:
        return JSONResponse(
            public_error_payload(exc, logger=logger), status_code=502, headers=_NO_STORE
        )
    return JSONResponse(
        {"status": "success", "elevation": elevation}, headers=_NO_STORE
    )


def mount_elevation_routes(app: Any, prefix: str = "") -> None:
    """Mount the console approval route onto a Starlette/FastAPI ``app``."""
    app.add_route(prefix + "/elevations/approve", elevation_approve, methods=["POST"])
