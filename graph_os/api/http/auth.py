"""HTTP identity adapter. Only server-verified authority reaches ``invoke``."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from graph_os.api.invoke import VerifiedCaller

CookieVerifier = Callable[[str], Awaitable[tuple["VerifiedCaller", str] | None]]


class HTTPAuthenticationError(PermissionError):
    """A request has no verified HTTP identity or a valid CSRF proof."""


class AmbientHTTPAuthenticator:
    """Use middleware-verified bearer authority; allow a wired cookie verifier.

    Cookie verification is intentionally unavailable until IDM-08 supplies the
    session store. A cookie by itself never grants authority.
    """

    def __init__(self, *, cookie_verifier: CookieVerifier | None = None) -> None:
        self.cookie_verifier = cookie_verifier

    async def authenticate(self, request: Request) -> VerifiedCaller:
        authorization = request.headers.get("authorization", "")
        cookie = request.cookies.get("__Host-graphos-session")
        if authorization and cookie:
            raise HTTPAuthenticationError("Choose one authentication method")
        if authorization:
            from graph_os.api.invoke import VerifiedCaller

            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token or not token.strip():
                raise HTTPAuthenticationError("Bearer credential required")
            # ActorIdentityMiddleware validates the bearer and mints these
            # ambient objects. The request header is never decoded here.
            from agent_utilities.knowledge_graph.core.session import resolve_session

            try:
                session = resolve_session()
                actor = session.actor
                if not actor.authenticated:
                    raise HTTPAuthenticationError("Verified identity required")
            except PermissionError as exc:
                raise HTTPAuthenticationError("Verified identity required") from exc
            return VerifiedCaller(
                principal=actor.actor_id,
                tenant=session.tenant,
                effective_scopes=frozenset(session.scopes),
                engine_claims=session.engine_verified_context(),
                principal_kind="service"
                if actor.actor_type.value == "automated_service"
                else "human",
                authenticated=True,
                credential_kind="bearer",
                policy_revision=str(session.policy_version),
                request_id=request.headers.get("x-request-id", "")[:128],
                session=session,
            )
        if cookie and self.cookie_verifier is not None:
            verified = await self.cookie_verifier(cookie)
            if verified is None:
                raise HTTPAuthenticationError("Verified identity required")
            caller, csrf_secret = verified
            if not caller.authenticated or not csrf_secret:
                raise HTTPAuthenticationError("Verified identity required")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                supplied = request.headers.get("x-csrf-token", "")
                if not supplied or not hmac.compare_digest(supplied, csrf_secret):
                    raise HTTPAuthenticationError("CSRF token required")
            return caller
        raise HTTPAuthenticationError("Verified identity required")
