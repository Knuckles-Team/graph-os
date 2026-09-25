"""Access services for the GraphOS operation registry.

The operation pipeline supplies the verified caller and tenant-bound EG client.
No handler accepts identity or tenant from operation parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_utilities.orchestration.action_policy import (
    ACTION_APPROVAL_KIND,
    approval_lease_to_props,
)
from agent_utilities.security.elevation import (
    ElevationApproval,
    ElevationRequest,
    ElevationRevocation,
    ElevationService,
    ElevationSurface,
)


def _elevations(context: Any) -> ElevationService:
    return ElevationService(context.client, caller=context.caller.principal)


def _console_claims(context: Any) -> Mapping[str, Any]:
    """Elevations use their existing exact-scope and direct-caller checks."""
    claims = dict(context.caller.engine_claims)
    claims["agent_id"] = context.caller.principal
    claims["scopes"] = sorted(context.caller.effective_scopes)
    if context.caller.delegated:
        claims["delegation"] = [context.caller.principal, "delegated"]
    return claims


async def request_elevation(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    ask = ElevationRequest.model_validate(params)
    return (await _elevations(context).request(ask)).model_dump(mode="json")


async def list_elevations(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    views = await _elevations(context).list_elevations()
    return {"elevations": [view.model_dump(mode="json") for view in views]}


async def revoke_elevation(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    revocation = ElevationRevocation.model_validate(params)
    return (await _elevations(context).revoke(revocation)).model_dump(mode="json")


async def approve_elevation(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    approval = ElevationApproval.model_validate(params)
    view = await _elevations(context).approve(
        approval,
        surface=ElevationSurface.OPERATOR_CONSOLE,
        claims=_console_claims(context),
    )
    return view.model_dump(mode="json")


async def deny_elevation(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Keep the unsupported design operation distinct from EG revoke."""
    raise NotImplementedError("ELEVATION_DENY_UNAVAILABLE: native EG deny is required")


async def list_leases(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    return await context.client.control_leases.list(
        tenant=context.caller.tenant,
        kind=params.get("kind"),
        status=params.get("status"),
        cursor=params.get("cursor"),
        limit=params.get("limit", 100),
    )


async def get_lease(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    lease = await context.client.control_leases.get(
        tenant=context.caller.tenant, lease_id=params["lease_id"]
    )
    if lease is None:
        raise LookupError("control lease is unavailable")
    return lease


async def check_access(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    allowed = await context.client.consensus.check_access(
        params["agent_id"], params["action"], graph=params["graph"]
    )
    return {"allowed": bool(allowed)}


async def explain_policy(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    return await context.client.query.explain_policy(params["plan"])


async def list_approvals(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    page = await context.client.control_leases.list(
        tenant=context.caller.tenant,
        kind=ACTION_APPROVAL_KIND,
        status=params.get("status", "active"),
        cursor=params.get("cursor"),
        limit=params.get("limit", 100),
    )
    return {
        "approvals": [
            approval_lease_to_props(lease) for lease in page.get("leases", [])
        ],
        "next_cursor": page.get("next_cursor"),
    }


async def get_approval(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    lease = await context.client.control_leases.get(
        tenant=context.caller.tenant, lease_id=params["approval_id"]
    )
    if lease is None or lease.get("kind") != ACTION_APPROVAL_KIND:
        raise LookupError("action approval is unavailable")
    return approval_lease_to_props(lease)


async def _decide_approval(context: Any, params: Mapping[str, Any], target: str) -> Any:
    approval_id = params["approval_id"]
    if not approval_id.startswith("action_approval:"):
        raise ValueError("approval_id must identify an action approval")
    lease = await context.client.control_leases.get(
        tenant=context.caller.tenant, lease_id=approval_id
    )
    if lease is None or lease.get("kind") != ACTION_APPROVAL_KIND:
        raise LookupError("action approval is unavailable")
    if lease.get("status") != "active":
        raise LookupError("action approval is no longer pending")
    return await context.client.control_leases.transition(
        tenant=context.caller.tenant,
        lease_id=approval_id,
        expected_revision=lease["revision"],
        to=target,
        idempotency_key=f"decide:{approval_id}",
    )


async def grant_approval(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    return await _decide_approval(context, params, "consumed")


async def deny_approval(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    return await _decide_approval(context, params, "revoked")
