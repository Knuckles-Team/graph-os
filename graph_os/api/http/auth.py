"""HTTP identity adapter. Only server-verified authority reaches ``invoke``."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from graph_os.api.invoke import VerifiedCaller


class HTTPAuthenticationError(PermissionError):
    """A request has no verified HTTP identity or a valid CSRF proof."""


class AmbientHTTPAuthenticator:
    """Use the identity gate's verified bearer or browser-session authority.

    The gate's session marker is set only after a live browser session and
    token-bound CSRF have been checked. A cookie value cannot set the marker.
    """

    async def authenticate(self, request: Request) -> VerifiedCaller:
        authorization = request.headers.get("authorization", "")
        request_id = request.headers.get("x-request-id", "")[:128]
        state = request.scope.get("state") or {}
        admitted_cookie = state.get("graphos_session_admitted") is True
        if not admitted_cookie and not authorization:
            raise HTTPAuthenticationError("Verified identity required")
        from graph_os.api.invoke import VerifiedCaller

        try:
            from agent_utilities.knowledge_graph.core.session import resolve_session

            session = resolve_session()
            if admitted_cookie:
                from graph_os.identity.browser import csrf_refusal, session_from_scope

                cookie = session_from_scope(request.scope)
                if cookie is None or csrf_refusal(request.scope, cookie) is not None:
                    raise HTTPAuthenticationError("Verified browser session required")
                claims = state.get("user_claims")
                if not isinstance(claims, Mapping) or (
                    claims.get("sub") != session.actor.actor_id
                    or claims.get("tenant_id") != session.tenant
                ):
                    raise HTTPAuthenticationError("Browser authority mismatch")
                fresh_mfa = state.get("graphos_console_mfa_at_ms")
                mfa_at_ms: int | None = None
                if isinstance(fresh_mfa, int) and not isinstance(fresh_mfa, bool):
                    age_ms = int(time.time() * 1000) - fresh_mfa
                    if 0 <= age_ms <= 15 * 60 * 1000:
                        mfa_at_ms = fresh_mfa
                # IdentityGate minted the ambient GraphSession from exactly
                # this broker-verified cookie. Its synthetic bearer is normal.
                return VerifiedCaller.from_session(
                    session, request_id=request_id, mfa_at_ms=mfa_at_ms
                )
            if any(
                key.lower() == b"cookie" for key, _ in request.scope.get("headers", ())
            ):
                # A client cannot combine an unadmitted browser cookie with a
                # bearer to sneak across the console boundary.
                from graph_os.identity.browser import session_from_scope

                if session_from_scope(request.scope) is not None:
                    raise HTTPAuthenticationError("Browser session not admitted")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token or not token.strip():
                raise HTTPAuthenticationError("Bearer credential required")
            # ActorIdentityMiddleware validates the bearer before minting the
            # ambient GraphSession; the header is never decoded here.
            return VerifiedCaller.from_session(session, request_id=request_id)
        except PermissionError as exc:
            raise HTTPAuthenticationError("Verified identity required") from exc
