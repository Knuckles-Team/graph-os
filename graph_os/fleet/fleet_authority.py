"""Exact-scope fleet authority for the served multiplexer (EH-629).

Every fleet operation names one kind, ``discover`` or ``delegate``, and the
caller must hold that kind's exact scope (``mcp:discover`` / ``mcp:delegate``)
plus any scope a child server declares in ``required_scopes``. No broader
scope substitutes for these: ``admin``, ``kg:admin`` and ``mcp:admin`` imply
nothing here, and one fleet scope does not imply the other.

The caller is resolved the same way for every transport:

* inside an HTTP request, the bearer FastMCP's auth provider validated. A
  request without one has no authority.
* outside an HTTP request (stdio, or a host helper such as the WebUI
  delegation seam), the verified :class:`GraphSession` bound to the current
  context. For a stdio server that is the process session minted at startup,
  so a local process runs with exactly its own minted scopes. With no bound
  session there is no authority.

The Eunomia policy filter (:mod:`graph_os.mcp_server.policy_filter`) reads the
same :class:`FleetCaller`, so scope checks and policy checks agree on who the
caller is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import fastmcp.exceptions as _fastmcp_exceptions

__all__ = [
    "FLEET_META_TOOL_KINDS",
    "FleetCaller",
    "FleetKind",
    "missing_fleet_scopes",
    "require_fleet_capability",
    "resolve_fleet_caller",
]


class FleetKind(StrEnum):
    """The bounded fleet operation kinds and the exact scope each requires."""

    DISCOVER = "discover"
    DELEGATE = "delegate"

    @property
    def scope(self) -> str:
        return f"mcp:{self.value}"


#: The resident fleet meta-tools and the fleet kind each one exercises. The
#: session-visibility filter uses this to hide a meta-tool from a caller whose
#: scopes the tool body would refuse, so list and call agree.
FLEET_META_TOOL_KINDS: dict[str, FleetKind] = {
    "find_tools": FleetKind.DISCOVER,
    "list_catalog": FleetKind.DISCOVER,
    "multiplexer_status": FleetKind.DISCOVER,
    "load_tools": FleetKind.DELEGATE,
    "unload_tools": FleetKind.DELEGATE,
}


@dataclass(frozen=True)
class FleetCaller:
    """The verified caller of one fleet or policy decision."""

    subject: str
    client_id: str
    tenant: str
    capabilities: frozenset[str]
    groups: tuple[str, ...]
    transport: str


def _bearer_caller(token: Any) -> FleetCaller:
    """Project a validated bearer onto a caller; raises when claims can't map."""
    scopes = frozenset(
        str(scope).strip()
        for scope in (getattr(token, "scopes", None) or [])
        if str(scope).strip()
    )
    client_id = str(getattr(token, "client_id", "") or "")
    claims = getattr(token, "claims", None)
    if not isinstance(claims, dict) or not claims:
        return FleetCaller(client_id, client_id, "", scopes, (), "http")
    from agent_utilities.security.request_identity import actor_from_claims

    actor = actor_from_claims(claims)
    return FleetCaller(
        subject=str(actor.actor_id),
        client_id=str(claims.get("azp") or client_id or actor.actor_id),
        tenant=str(actor.tenant_id),
        capabilities=scopes | frozenset(str(role) for role in actor.roles),
        groups=tuple(actor.groups),
        transport="http",
    )


def _http_caller() -> FleetCaller | None:
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception:
        return None
    if token is None:
        return None
    try:
        return _bearer_caller(token)
    except Exception:
        raise _fastmcp_exceptions.ToolError(
            "Verified capability mapping unavailable"
        ) from None


def _session_caller() -> FleetCaller | None:
    """The verified session bound to this context (the stdio process session)."""
    from agent_utilities.api import current_session

    session = current_session()
    actor = getattr(session, "actor", None)
    if actor is None or not getattr(actor, "authenticated", False):
        return None
    return FleetCaller(
        subject=str(actor.actor_id),
        client_id=str(actor.actor_id),
        tenant=str(getattr(session, "tenant", "") or actor.tenant_id),
        capabilities=frozenset(str(role) for role in actor.roles)
        | frozenset(str(scope) for scope in getattr(session, "scopes", ())),
        groups=tuple(actor.groups),
        transport="local",
    )


def resolve_fleet_caller() -> FleetCaller | None:
    """The verified caller for this context, or ``None`` when there is none."""
    try:
        from fastmcp.server.dependencies import get_http_request

        get_http_request()
    except RuntimeError:
        return _session_caller()
    except Exception:
        return None
    return _http_caller()


def missing_fleet_scopes(
    kind: FleetKind | str,
    extra_scopes: list[str] | tuple[str, ...] = (),
    capabilities: frozenset[str] | None = None,
) -> frozenset[str]:
    """The exact scopes ``kind`` (plus ``extra_scopes``) needs that are not held.

    ``capabilities`` defaults to the resolved caller's; no caller holds nothing.
    """
    fleet_kind = FleetKind(kind)
    if capabilities is None:
        caller = resolve_fleet_caller()
        capabilities = caller.capabilities if caller is not None else frozenset()
    return frozenset({fleet_kind.scope, *extra_scopes}) - capabilities


def require_fleet_capability(
    kind: FleetKind | str, extra_scopes: list[str] | None = None
) -> None:
    """Refuse the current fleet operation unless the caller holds exact scopes."""
    try:
        fleet_kind = FleetKind(kind)
    except ValueError:
        raise _fastmcp_exceptions.ToolError("Unknown MCP fleet operation") from None
    missing = missing_fleet_scopes(fleet_kind, tuple(extra_scopes or ()))
    if fleet_kind.scope in missing:
        raise _fastmcp_exceptions.ToolError(
            f"MCP fleet {fleet_kind.value} capability required"
        )
    if missing:
        raise _fastmcp_exceptions.ToolError("Child MCP capability scope required")
