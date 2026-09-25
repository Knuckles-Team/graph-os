"""The identity gate: the ASGI layer every served request crosses first.

It owns the identity endpoints (``/auth/*``, ``/.well-known/*``,
``/oauth/token``) — they are answered here and never forwarded — and for
every other request runs admission (:mod:`graph_os.identity.admission`),
replacing the request's ``Authorization`` header with the admitted
local-issuer token before the unchanged downstream identity gate verifies it.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from typing import Any

from starlette.routing import Route, Router

from .admission import Admission, AdmissionService
from .browser import csrf_refusal, origin_refusal, session_from_scope
from .engine import IdentityUnavailable

__all__ = ["IdentityGate", "OWNED_PREFIXES", "owned_route_refusal"]

logger = logging.getLogger(__name__)

#: Paths the gate answers itself.
OWNED_PREFIXES = ("/auth/", "/.well-known/", "/oauth/token", "/scim/v2/")

#: Owned routes that carry their own proof and no browser session, so no
#: Origin/CSRF rule applies: a SCIM provisioner's API key, the token
#: exchange's subject credential, and the IdP's signed SAML response (its
#: transaction cookie + ``InResponseTo`` are the cross-site defence).
_SELF_AUTHENTICATED = ("/scim/v2/", "/oauth/token", "/auth/saml/acs")
#: Forms submitted before any session exists: a same-origin ``Origin`` is
#: the whole CSRF defence (there is no session to bind a token to yet).
_PRE_SESSION = frozenset(
    {"/auth/login", "/auth/setup", "/auth/password/forgot", "/auth/password/reset"}
)
_LDAP_LOGIN = re.compile(r"^/auth/ldap/[^/]+/login$")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def owned_route_refusal(scope: Scope) -> str | None:
    """Why a state-changing request to an owned route is refused as cross-site.

    Self-authenticated routes are exempt; pre-session forms need a
    same-origin ``Origin``; everything else that carries a session cookie
    needs the session's CSRF token too.
    """
    path = str(scope.get("path") or "")
    if str(scope.get("method") or "GET").upper() in _SAFE_METHODS:
        return None
    if path.startswith(_SELF_AUTHENTICATED) or path.rstrip("/") in _SELF_AUTHENTICATED:
        return None
    session = session_from_scope(scope)
    if session is None or path in _PRE_SESSION or _LDAP_LOGIN.match(path):
        return origin_refusal(scope)
    return csrf_refusal(scope, session)


def _owned(path: str) -> bool:
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in OWNED_PREFIXES
    )


def _with_token(scope: Scope, token: str, claims: dict[str, Any]) -> Scope:
    """Present ``token`` downstream, with its claims marked as verified here.

    The claims ride ``scope["state"]["user_claims"]`` (``auth_type="jwt"``),
    the seam an outer authentication boundary uses to hand verified claims to
    the agent-utilities identity gate, so the downstream gate projects the
    same actor without re-fetching this issuer's keys over HTTP.
    """
    headers = [
        (k, v) for k, v in scope.get("headers") or [] if k.lower() != b"authorization"
    ]
    headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    state = {
        **(scope.get("state") or {}),
        "user_claims": {**claims, "auth_type": "jwt"},
    }
    return {**scope, "headers": headers, "state": state}


def _without_console_markers(scope: Scope) -> Scope:
    """Discard caller-supplied state before the gate makes a trusted decision."""
    state = dict(scope.get("state") or {})
    state.pop("graphos_session_admitted", None)
    state.pop("graphos_console_mfa_at_ms", None)
    return {**scope, "state": state}


async def _refuse(scope: Scope, send: Send, status: int, reason: str) -> None:
    if scope.get("type") == "websocket":
        await send({"type": "websocket.close", "code": 4000 + status})
        return
    body = json.dumps({"error": reason}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _decorated_send(send: Send, extra: Sequence[tuple[bytes, bytes]]) -> Send:
    if not extra:
        return send

    async def wrapped(message: MutableMapping[str, Any]) -> None:
        if message.get("type") == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return wrapped


class IdentityGate:
    """ASGI middleware: identity endpoints + per-request admission."""

    def __init__(
        self, app: ASGIApp, *, admission: AdmissionService, routes: Sequence[Route]
    ) -> None:
        self.app = app
        self._admission = admission
        self._router = Router(routes=list(routes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope.get("type") == "http" and _owned(str(scope.get("path") or "")):
            await self._serve_owned(scope, receive, send)
            return
        try:
            admission = await self._admission.admit(scope)
        except (IdentityUnavailable, PermissionError) as exc:
            logger.warning("identity admission unavailable (%s)", type(exc).__name__)
            await _refuse(scope, send, 503, "identity_unavailable")
            return
        await self._forward(admission, scope, receive, send)

    async def _serve_owned(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            mode = await self._admission.mode()
        except IdentityUnavailable:
            await _refuse(scope, send, 503, "identity_unavailable")
            return
        reason = owned_route_refusal(scope)
        if reason is None and mode == "none":
            reason = self._admission.none_guard.refusal(scope)
        if reason is not None:
            await _refuse(scope, send, 403, reason)
            return
        await self._router(scope, receive, send)

    async def _forward(
        self, admission: Admission, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if admission.refusal is not None:
            await _refuse(scope, send, *admission.refusal)
            return
        forwarded = _without_console_markers(scope)
        if admission.token:
            claims = self._admission.broker.issuer.verify(admission.token)
            forwarded = _with_token(forwarded, admission.token, claims)
            if admission.session_principal_id is not None:
                if (
                    claims.get("sub") != admission.session_principal_id
                    or not isinstance(claims.get("tenant_id"), str)
                    or not claims["tenant_id"]
                ):
                    await _refuse(scope, send, 503, "identity_unavailable")
                    return
                forwarded["state"]["graphos_session_admitted"] = True
                if admission.session_mfa_at_ms is not None:
                    forwarded["state"]["graphos_console_mfa_at_ms"] = (
                        admission.session_mfa_at_ms
                    )
        await self.app(forwarded, receive, _decorated_send(send, admission.set_headers))
