"""Browser session transport: the session cookie and token-bound CSRF (IDM-08).

The browser holds only an opaque 256-bit session id in a ``__Host-`` cookie
(``Secure; HttpOnly; SameSite=Lax; Path=/``); the engine holds only its hash,
so revoking a session server-side takes effect on the very next request.

Cookie-authenticated state changes need BOTH a same-origin ``Origin`` header
(a request without one is refused) and the CSRF token, which is a one-way
digest of the session id: a page on another origin can neither read the
cookie nor compute the token, and every GraphOS replica derives the same value
without shared state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "clear_cookie_header",
    "csrf_refusal",
    "csrf_token_for",
    "origin_refusal",
    "request_header",
    "session_cookie_header",
    "session_from_scope",
]

SESSION_COOKIE = "__Host-graphos_session"
CSRF_HEADER = b"x-csrf-token"
_CSRF_DOMAIN = b"graph-os/identity/csrf/v1\0"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_COOKIE_ATTRIBUTES = "Path=/; Secure; HttpOnly; SameSite=Lax"
#: A session id as GraphOS mints it (URL-safe base64); anything else is ignored.
_SESSION_SHAPE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def csrf_token_for(session_token: str) -> str:
    """The CSRF token bound to one session id (a one-way digest of it)."""
    digest = hashlib.sha256(_CSRF_DOMAIN + session_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def request_header(scope: Mapping[str, Any], name: bytes) -> list[str]:
    """Every value of one request header (a list: duplicates are a signal)."""
    return [
        value.decode("latin-1").strip()
        for key, value in scope.get("headers") or []
        if key.lower() == name
    ]


def _cookies(scope: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for raw in request_header(scope, b"cookie"):
        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(raw)
        except CookieError:
            continue
        for name, morsel in parsed.items():
            yield name, morsel.value


def session_from_scope(scope: Mapping[str, Any]) -> str | None:
    """The session id cookie; ``None`` when absent or ambiguous (duplicated)."""
    values = [value for name, value in _cookies(scope) if name == SESSION_COOKIE]
    if len(values) != 1 or not _SESSION_SHAPE.match(values[0]):
        return None
    return values[0]


def session_cookie_header(session_token: str, max_age_seconds: int) -> tuple[bytes, bytes]:
    cookie = f"{SESSION_COOKIE}={session_token}; Max-Age={max_age_seconds}; {_COOKIE_ATTRIBUTES}"
    return b"set-cookie", cookie.encode("latin-1")


def clear_cookie_header() -> tuple[bytes, bytes]:
    cookie = f"{SESSION_COOKIE}=; Max-Age=0; {_COOKIE_ATTRIBUTES}"
    return b"set-cookie", cookie.encode("latin-1")


def _same_origin(scope: Mapping[str, Any], origin: str) -> bool:
    hosts = request_header(scope, b"host")
    if len(hosts) != 1:
        return False
    parsed = urlsplit(origin)
    scheme = str(scope.get("scheme") or "http").lower()
    expected = "https" if scheme in {"https", "wss"} else "http"
    return parsed.scheme.lower() == expected and parsed.netloc.lower() == hosts[0].lower()


def origin_refusal(scope: Mapping[str, Any]) -> str | None:
    """``None`` only when the request carries exactly one same-origin Origin."""
    origins = request_header(scope, b"origin")
    if len(origins) != 1 or not _same_origin(scope, origins[0]):
        return "origin_not_same_origin"
    return None


def csrf_refusal(scope: Mapping[str, Any], session_token: str) -> str | None:
    """Why a cookie-authenticated request is refused as cross-site, or ``None``."""
    method = str(scope.get("method") or "GET").upper()
    websocket = scope.get("type") == "websocket"
    if not websocket and method in _SAFE_METHODS:
        return None
    origin = origin_refusal(scope)
    if origin is not None or websocket:
        return origin
    presented = request_header(scope, CSRF_HEADER)
    expected = csrf_token_for(session_token)
    if len(presented) != 1 or not hmac.compare_digest(presented[0], expected):
        return "csrf_token_mismatch"
    return None
