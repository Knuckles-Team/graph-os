"""Project supplied, request-bound verified session authority into B's caller."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from graph_os.api.invoke import VerifiedCaller


def caller_from_session(
    session: Any,
    *,
    credential_kind: str,
    request_id: str = "",
    mfa_at_ms: int | None = None,
) -> VerifiedCaller:
    """Copy verified facts without deriving grants or classifying unknown actors.

    The composition root must supply a session resolved by the verified authority
    for this request. This function does not authenticate client-provided objects.
    Credential kind is supplied by that authority, never inferred from actor kind.
    """
    from graph_os.api.invoke import VerifiedCaller

    if session is None or not isinstance(credential_kind, str) or not credential_kind:
        raise PermissionError("Verified session authority required")
    session.ensure_authority_current()
    actor = session.actor
    actor.ensure_credential_current()
    if actor.authenticated is not True or actor.actor_type not in {"human", "service"}:
        raise PermissionError("Verified principal kind required")
    claims = session.engine_verified_context()
    scopes = session.scopes
    if (
        not isinstance(claims, Mapping)
        or not isinstance(scopes, (tuple, list, set, frozenset))
        or any(
            not isinstance(scope, str) or not scope or "*" in scope for scope in scopes
        )
        or not isinstance(claims.get("scopes"), (tuple, list, set, frozenset))
        or any(not isinstance(scope, str) for scope in claims["scopes"])
        or frozenset(claims["scopes"]) != frozenset(scopes)
        or claims.get("principal") != actor.actor_id
        or claims.get("tenant") != session.tenant
        or actor.tenant_id != session.tenant
        or claims.get("policy_version") != session.policy_version
        or not isinstance(claims.get("delegation"), (tuple, list))
        or any(
            not isinstance(value, str) or not value.strip()
            for value in (actor.actor_id, session.tenant, session.policy_version)
        )
    ):
        raise PermissionError("Verified session authority mismatch")
    if mfa_at_ms is not None and type(mfa_at_ms) is not int:
        raise PermissionError("Verified step-up authority required")
    return VerifiedCaller(
        principal=actor.actor_id,
        tenant=session.tenant,
        effective_scopes=frozenset(scopes),
        engine_claims=claims,
        principal_kind=actor.actor_type,
        authenticated=True,
        delegated=bool(claims["delegation"]),
        credential_kind=credential_kind,
        policy_revision=session.policy_version,
        request_id=request_id,
        mfa_at_ms=mfa_at_ms,
        session=session,
    )


class VerifiedMCPCaller:
    """Synchronous callback compatible with ``MCPProjection``.

    Both resolvers are mandatory, server-owned, and request scoped. They must
    resolve the same currently verified credential; a process-wide identity is
    not a fallback for an unauthenticated remote request. A stdio process session
    must be explicitly bound by its verified authority before calling this port.
    """

    def __init__(
        self,
        *,
        session_for_request: Callable[[], Any],
        credential_kind_for_request: Callable[[], str],
    ) -> None:
        if not callable(session_for_request) or not callable(
            credential_kind_for_request
        ):
            raise ValueError("Verified MCP authority resolvers required")
        self._session_for_request = session_for_request
        self._credential_kind_for_request = credential_kind_for_request

    def caller_for_request(self) -> VerifiedCaller | None:
        """Resolve afresh on every discovery or invocation; denial has no caller."""
        try:
            return caller_from_session(
                self._session_for_request(),
                credential_kind=self._credential_kind_for_request(),
            )
        except PermissionError:
            return None
