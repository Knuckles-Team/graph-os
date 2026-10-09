"""Opaque cookie checks and private request snapshots, not live authority.

The qualified session owner must check EG revocation, expiry, rotation and MFA
before exporting the existing WebUI BrowserSessionEvidence. These primitives
do not implement that port or allow request headers/state to install proof.
"""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .engine import IdentityUnavailable

SESSION_COOKIE = "__Host-graphos_session"
_COOKIE_FLAGS = "Secure; HttpOnly; SameSite=Lax; Path=/"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_CSRF_DOMAIN = b"graph-os/identity/csrf/v1\0"


def _headers(scope: Mapping[str, Any], name: bytes) -> tuple[bytes, ...]:
    headers = scope.get("headers")
    if not isinstance(headers, (tuple, list)) or type(headers) not in (tuple, list):
        raise PermissionError("request headers are missing")
    if any(not _is_header_pair(pair) for pair in headers):
        raise PermissionError("request headers are malformed")
    return tuple(value for key, value in headers if key.lower() == name)


def _is_header_pair(pair: object) -> bool:
    return (
        type(pair) in (tuple, list)
        and isinstance(pair, (tuple, list))
        and len(pair) == 2
        and type(pair[0]) is bytes
        and type(pair[1]) is bytes
    )


def _valid_token(token: str) -> bool:
    if type(token) is not str or not _TOKEN.fullmatch(token):
        return False
    decoded = base64.urlsafe_b64decode(token + "=")
    return len(decoded) == 32 and (
        base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") == token
    )


def session_from_scope(scope: Mapping[str, Any]) -> str | None:
    """Absent is None; a presented invalid/ambiguous/legacy cookie refuses."""
    found: list[str] = []
    for header in _headers(scope, b"cookie"):
        for part in header.decode("latin-1").split(";"):
            _collect_session_cookie(part, found)
    if not found:
        return None
    if len(found) != 1 or not _valid_token(found[0]):
        raise PermissionError("session cookie is invalid or ambiguous")
    return found[0]


def _collect_session_cookie(part: str, found: list[str]) -> None:
    name, separator, value = part.strip().partition("=")
    normalized_name = name.strip()
    if normalized_name == "au_session" or normalized_name.startswith("au_session."):
        raise PermissionError("legacy browser credential is unsupported")
    if normalized_name == SESSION_COOKIE:
        if name != normalized_name or not separator:
            raise PermissionError("session cookie is malformed")
        found.append(value)


def csrf_token_for(token: str) -> str:
    if not _valid_token(token):
        raise PermissionError("session cookie is invalid")
    digest = hashlib.sha256(_CSRF_DOMAIN + token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def require_mutation_proof(scope: Mapping[str, Any], *, trusted_origin: str) -> None:
    """Check transport proof, never claim the opaque session is live."""
    _require_trusted_origin(trusted_origin)
    method = _require_http_method(scope)
    token = session_from_scope(scope)
    if token is None:
        raise PermissionError("opaque GraphOS session required")
    origins = _headers(scope, b"origin")
    expected_origin = trusted_origin.encode("ascii")
    if origins and origins != (expected_origin,):
        raise PermissionError("configured exact origin required")
    if method in _SAFE_METHODS:
        return
    if origins != (expected_origin,):
        raise PermissionError("configured exact origin required")
    _require_csrf(scope, token)


def _require_trusted_origin(trusted_origin: str) -> None:
    if type(trusted_origin) is not str or not trusted_origin.isascii():
        raise IdentityUnavailable("exact trusted origin is not configured")
    parsed = urlsplit(trusted_origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IdentityUnavailable("exact trusted origin is not configured")
    if any(
        (parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment)
    ):
        raise IdentityUnavailable("exact trusted origin is not configured")


def _require_http_method(scope: Mapping[str, Any]) -> str:
    method = scope.get("method")
    if scope.get("type") != "http" or type(method) is not str or not method:
        raise PermissionError("an explicit HTTP method is required")
    if method != method.upper():
        raise PermissionError("HTTP method is malformed")
    return method


def _require_csrf(scope: Mapping[str, Any], token: str) -> None:
    presented = _headers(scope, b"x-csrf-token")
    expected = csrf_token_for(token).encode("ascii")
    if len(presented) != 1 or not hmac.compare_digest(presented[0], expected):
        raise PermissionError("session-bound mutation CSRF required")


def session_cookie_header(token: str, *, expires_at_ms: int, now_ms: int) -> bytes:
    """Bound cookie lifetime by the authoritative effective session deadline."""
    if not _valid_token(token):
        raise PermissionError("session cookie is invalid")
    if any(type(value) is not int or value < 0 for value in (expires_at_ms, now_ms)):
        raise IdentityUnavailable("authoritative session expiry and clock required")
    seconds = (expires_at_ms - now_ms) // 1000
    if seconds <= 0:
        raise PermissionError("session cannot support a live cookie")
    return f"{SESSION_COOKIE}={token}; Max-Age={seconds}; {_COOKIE_FLAGS}".encode()


def _require_routing_fields(scope: Mapping[str, Any]) -> tuple[Any, ...]:
    """The required `type`/`method`/`path` triple, or refuse."""
    values = tuple(scope.get(name) for name in ("type", "method", "path"))
    if any(type(value) is not str or not value for value in values):
        raise PermissionError("request routing fields are missing")
    return values


def _require_routing_context(scope: Mapping[str, Any]) -> tuple[str, bytes, bytes, str | None]:
    """The exact target/root/scheme fields, or refuse."""
    raw_path, query = scope.get("raw_path"), scope.get("query_string")
    if type(raw_path) is not bytes or type(query) is not bytes:
        raise PermissionError("exact request target is missing")
    root_path = scope.get("root_path", "")
    scheme = scope.get("scheme")
    if type(root_path) is not str or (scheme is not None and type(scheme) is not str):
        raise PermissionError("request routing context is malformed")
    return root_path, raw_path, query, scheme


def _require_server_context(scope: Mapping[str, Any]) -> tuple[str, int] | None:
    """The exact `(host, port)` server pair, or refuse a malformed one."""
    server = scope.get("server")
    if server is None:
        return None
    if (
        type(server) not in (tuple, list)
        or len(server) != 2
        or type(server[0]) is not str
        or type(server[1]) is not int
    ):
        raise PermissionError("request server context is malformed")
    return tuple(server)


def _header_digests(scope: Mapping[str, Any], key: bytes) -> tuple[bytes, ...]:
    """Digest exact security headers without ever retaining their secrets."""
    digests = []
    for name in (b"host", b"origin", b"cookie", b"authorization", b"x-csrf-token"):
        values_for_header = _headers(scope, name)
        material = b"".join(
            len(value).to_bytes(8, "big") + value for value in values_for_header
        )
        digests.append(hmac.digest(key, name + b"\0" + material, "sha256"))
    return tuple(digests)


def _snapshot(scope: Mapping[str, Any], key: bytes) -> tuple[Any, ...]:
    """Copy routing fields and digest exact security headers without secrets."""
    values = _require_routing_fields(scope)
    root_path, raw_path, query, scheme = _require_routing_context(scope)
    server = _require_server_context(scope)
    digests = _header_digests(scope, key)
    return (*values, root_path, raw_path, query, scheme, server, *digests)


@dataclass(frozen=True, slots=True, repr=False)
class _RequestBinding:
    """Owner-private snapshot; never a public evidence DTO or header proof.

    Capture only after qualified credential resolution and trusted forwarding.
    The request owner must keep this object private, recheck after each awaited
    authority call and before dispatch, and discard it in a finally block.
    It neither owns live session state nor replaces those authority checks.
    """

    _scope: Any = field(compare=False)
    _session: Any = field(compare=False)
    _session_ref: str
    _key: bytes
    _snapshot: tuple[Any, ...]
    _token_digest: bytes

    @classmethod
    def capture(
        cls,
        scope: Mapping[str, Any],
        session: Any,
        *,
        session_ref: str,
        forwarded_token: str,
    ) -> "_RequestBinding":
        if session is None or type(session_ref) is not str or not session_ref:
            raise IdentityUnavailable("qualified caller/session binding required")
        if type(forwarded_token) is not str or not forwarded_token:
            raise IdentityUnavailable("verified forwarded token required")
        key = secrets.token_bytes(32)
        return cls(
            scope,
            session,
            session_ref,
            key,
            _snapshot(scope, key),
            hmac.digest(key, forwarded_token.encode(), "sha256"),
        )

    def ensure_unchanged(
        self,
        scope: Mapping[str, Any],
        session: Any,
        *,
        session_ref: str,
        forwarded_token: str,
    ) -> None:
        if (
            scope is not self._scope
            or session is not self._session
            or session_ref != self._session_ref
            or type(forwarded_token) is not str
            or _snapshot(scope, self._key) != self._snapshot
            or not hmac.compare_digest(
                hmac.digest(self._key, forwarded_token.encode(), "sha256"),
                self._token_digest,
            )
        ):
            raise PermissionError("request credential/session binding changed")
