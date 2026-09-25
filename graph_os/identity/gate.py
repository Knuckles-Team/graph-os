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
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from typing import Any

from starlette.routing import Route, Router

from .admission import Admission, AdmissionService
from .engine import IdentityUnavailable

__all__ = ["IdentityGate", "OWNED_PREFIXES"]

logger = logging.getLogger(__name__)

#: Paths the gate answers itself.
OWNED_PREFIXES = ("/auth/", "/.well-known/", "/oauth/token")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


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
        if mode == "none":
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
        forwarded = scope
        if admission.token:
            claims = self._admission.broker.issuer.verify(admission.token)
            forwarded = _with_token(scope, admission.token, claims)
        await self.app(forwarded, receive, _decorated_send(send, admission.set_headers))
