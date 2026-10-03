"""HTTP caller projection over explicitly supplied verified request authorities."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import Request

from graph_os.api.mcp.caller import caller_from_session

if TYPE_CHECKING:
    from agent_utilities.security.request_identity import VerifiedLocalBearer

    from graph_os.api.invoke import VerifiedCaller


class HTTPAuthenticationError(PermissionError):
    """A request has no verified HTTP identity or a valid CSRF proof."""


class AmbientHTTPAuthenticator:
    """Decide console eligibility for an already-verified caller.

    The gate's session marker is set only after a live browser session and
    token-bound CSRF have been checked. A cookie value cannot set the marker.
    """

    def __init__(
        self,
        *,
        console_origin: str | None = None,
        session_for_request: Callable[[Request], Awaitable[Any]] | None = None,
        verify_browser_session: Callable[[Request, Any], Awaitable[int | None]]
        | None = None,
    ) -> None:
        # Configured at the server composition root, never from a request.
        self.console_origin = console_origin
        if session_for_request is not None and not callable(session_for_request):
            raise ValueError("Verified HTTP session resolver required")
        if verify_browser_session is not None and not callable(verify_browser_session):
            raise ValueError("Verified browser session resolver required")
        self._session_for_request = session_for_request
        self._verify_browser_session = verify_browser_session

    def is_console_request(self, request: Request, caller: Any) -> bool:
        """Only a fresh attended browser session may use Surface.CONSOLE."""
        state = request.scope.get("state") or {}
        if state.get("graphos_session_admitted") is not True:
            return False
        if not request.cookies.get("__Host-graphos_session"):
            return False
        if (
            not self.console_origin
            or request.headers.get("origin") != self.console_origin
        ):
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
        """Resolve the authority already verified and bound to this request.

        ``session_for_request`` must reject credentials not verified for this
        exact request; mere ambient process authority is insufficient. Browser
        verification must check the live cookie, CSRF and session subject/tenant
        binding, returning only a verified MFA timestamp (or None). Missing
        authority refuses; headers and request state never manufacture a caller.
        """
        if self._session_for_request is None:
            raise HTTPAuthenticationError("Verified HTTP authority unavailable")
        state = request.scope.get("state") or {}
        cookie = request.cookies.get("__Host-graphos_session")
        admitted = state.get("graphos_session_admitted") is True
        if cookie is not None or admitted:
            if not cookie or not admitted or self._verify_browser_session is None:
                raise HTTPAuthenticationError("Verified browser authority required")
            kind = "session"
        else:
            authorization = request.headers.getlist("authorization")
            if len(authorization) != 1:
                raise HTTPAuthenticationError("Verified bearer required")
            scheme, _, token = authorization[0].partition(" ")
            if (
                scheme.lower() != "bearer"
                or not token
                or any(c.isspace() for c in token)
            ):
                raise HTTPAuthenticationError("Verified bearer required")
            kind = "bearer"
        try:
            session = await self._session_for_request(request)
            mfa_at_ms = None
            if kind == "session":
                # The presence and callability of this authority were checked above.
                verifier = self._verify_browser_session
                if verifier is None:
                    raise HTTPAuthenticationError("Verified browser authority required")
                mfa_at_ms = await verifier(request, session)
            return caller_from_session(
                session,
                credential_kind=kind,
                request_id=request.headers.get("x-request-id", "")[:128],
                mfa_at_ms=mfa_at_ms,
            )
        except PermissionError:
            raise HTTPAuthenticationError("Verified identity required") from None


async def verify_local_bearer_request(request: Request) -> VerifiedLocalBearer:
    """Verify this request's exact local token; do not manufacture a caller.

    The result lacks current policy/delegation/engine authority. Composition
    must obtain those from the qualified owner before producing a session.
    No ambient identity or legacy actor/session minter is consulted here.
    """
    from agent_utilities.security.auth import parse_bearer_authorization
    from agent_utilities.security.request_identity import verify_local_bearer_token

    try:
        token = parse_bearer_authorization(
            [
                value
                for key, value in request.scope.get("headers", ())
                if key.lower() == b"authorization"
            ]
        )
        if token is None:
            raise PermissionError("Presented bearer required")
        return await verify_local_bearer_token(token)
    except PermissionError:
        raise HTTPAuthenticationError("Verified local bearer required") from None


class BoundBrowserVerifier:
    """Bind the WebUI exporter to an explicit qualified live-session owner."""

    def __init__(self, *, authority: Any, console_origin: str) -> None:
        if not callable(getattr(authority, "verify_request", None)):
            raise ValueError("Verified browser session authority required")
        if not isinstance(console_origin, str) or not console_origin:
            raise ValueError("Verified browser origin required")
        self._authority = authority
        self._console_origin = console_origin

    async def __call__(self, request: Request, session: Any) -> int | None:
        from agent_webui.oidc_session import verify_browser_session

        return await verify_browser_session(
            request.scope,
            session,
            authority=self._authority,
            console_origin=self._console_origin,
        )
