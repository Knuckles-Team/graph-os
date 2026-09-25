"""HTTP identity adapter. Only server-verified authority reaches ``invoke``."""

from __future__ import annotations

import hmac
import time
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

    def __init__(
        self,
        *,
        cookie_verifier: CookieVerifier | None = None,
        console_origin: str | None = None,
    ) -> None:
        self.cookie_verifier = cookie_verifier
        # This is configured by the server, never supplied by the caller.
        self.console_origin = console_origin

    def is_console_request(self, request: Request, caller: VerifiedCaller) -> bool:
        """Classify a verified, attended browser request as the console surface."""

        if (
            not self.console_origin
            or request.headers.get("origin") != self.console_origin
        ):
            return False
        if not request.cookies.get("__Host-graphos-session"):
            return False
        if request.headers.get("authorization"):
            return False
        if (
            caller.credential_kind != "session"
            or caller.principal_kind != "human"
            or caller.delegated
            or caller.mfa_at_ms is None
        ):
            return False
        age_ms = int(time.time() * 1000) - caller.mfa_at_ms
        return 0 <= age_ms <= 900_000

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
                principal_kind="human" if actor.actor_type.value == "human" else "service",
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
