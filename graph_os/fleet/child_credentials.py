"""GraphOS's outbound service identity for authenticated MCP fleet children.

RF-ADR-009 assigns authenticated fleet admission to ``graph_os.fleet``. When a
remote child enforces authentication, the multiplexer presents one service
identity selected by ``MCP_CLIENT_AUTH``:

* ``oidc-client-credentials`` -- a self-refreshing OAuth2 client-credentials
  bearer minted by the agent-connector-sdk (``OIDC_*`` settings);
* ``basic`` -- HTTP Basic from ``MCP_BASIC_AUTH_USERNAME`` and the secret
  reference ``MCP_BASIC_AUTH_PASSWORD_REF``;
* ``rotating-file-bearer`` -- a bearer re-read on every request from the
  mode-0600 file ``MCP_BEARER_TOKEN_FILE`` that an out-of-process refresher
  rotates, so a long-lived session renews in band;
* ``none`` (default) -- no service identity.

A child that declares its own ``Authorization`` header is never overridden.
A selected mode that is incomplete fails closed; it never degrades to an
anonymous request. Every returned auth is both an ``httpx.Auth`` and an
``httpx2.Auth`` so the SSE and streamable-HTTP transports accept it as is.
"""

from __future__ import annotations

import base64
import functools
import stat
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from pathlib import Path
from typing import Any

import anyio
import httpx
import httpx2
from agent_connector_sdk.auth.client_credentials import ClientCredentialsAuth
from agent_connector_sdk.auth.oidc import (
    ClientCredentialsConfig,
    client_credentials_auth,
)
from agent_connector_sdk.config import setting
from agent_connector_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from agent_connector_sdk.credentials.resolution import resolve_secret_reference
from pydantic import ValidationError

__all__ = [
    "ChildAuthConfigurationError",
    "HeaderAuth",
    "child_auth",
    "outbound_auth_configuration_status",
    "read_rotating_bearer_token",
    "service_session_max_age",
]

MODE_OIDC = "oidc-client-credentials"
MODE_BASIC = "basic"
MODE_ROTATING_FILE_BEARER = "rotating-file-bearer"
MODE_NONE = "none"

_MAX_BEARER_FILE_BYTES = 65_536
_MAX_TEXT_FIELD = 4_096
#: Recycle a service-authenticated session this long before its bearer expires.
_SESSION_EXPIRY_MARGIN_S = 35.0
#: Lifetime assumed before the first mint reveals the issuer's real TTL.
_ASSUMED_TOKEN_TTL_S = 300.0
#: Never recycle a child session more aggressively than this.
_MIN_SESSION_MAX_AGE_S = 20.0


class ChildAuthConfigurationError(RuntimeError):
    """The selected outbound child identity is unsupported or incomplete."""


def _text(name: str) -> str:
    return str(setting(name, "") or "").strip()


def _auth_mode() -> str:
    mode = (_text("MCP_CLIENT_AUTH") or MODE_NONE).lower()
    if mode not in _MODE_CONFIG_PROBLEMS:
        raise ChildAuthConfigurationError("MCP_CLIENT_AUTH has an unsupported value")
    return mode


def _bad_text(value: str, forbidden: str = "\r\n\x00") -> bool:
    return len(value) > _MAX_TEXT_FIELD or any(char in value for char in forbidden)


def _bad_reference(name: str) -> bool:
    try:
        parse_secret_reference(_text(name))
    except SecretReferenceError:
        return True
    return False


def _missing(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if not _text(name)]


def _oidc_problems() -> tuple[list[str], list[str]]:
    missing = _missing(("OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET_REF", "OIDC_AUDIENCE"))
    if not (_text("OIDC_TOKEN_URL") or _text("OIDC_ISSUER")):
        missing.append("OIDC_TOKEN_URL_OR_OIDC_ISSUER")
    invalid = [
        name for name in ("OIDC_CLIENT_ID", "OIDC_AUDIENCE") if _bad_text(_text(name))
    ]
    if "OIDC_CLIENT_SECRET_REF" not in missing and _bad_reference(
        "OIDC_CLIENT_SECRET_REF"
    ):
        invalid.append("OIDC_CLIENT_SECRET_REF")
    return missing, invalid


def _basic_problems() -> tuple[list[str], list[str]]:
    missing = _missing(("MCP_BASIC_AUTH_USERNAME", "MCP_BASIC_AUTH_PASSWORD_REF"))
    invalid = []
    if _bad_text(_text("MCP_BASIC_AUTH_USERNAME"), "\r\n\x00:"):
        invalid.append("MCP_BASIC_AUTH_USERNAME")
    if "MCP_BASIC_AUTH_PASSWORD_REF" not in missing and _bad_reference(
        "MCP_BASIC_AUTH_PASSWORD_REF"
    ):
        invalid.append("MCP_BASIC_AUTH_PASSWORD_REF")
    return missing, invalid


def _rotating_problems() -> tuple[list[str], list[str]]:
    missing = _missing(("MCP_BEARER_TOKEN_FILE",))
    invalid = (
        ["MCP_BEARER_TOKEN_FILE"] if _bad_text(_text("MCP_BEARER_TOKEN_FILE")) else []
    )
    return missing, invalid


def _no_problems() -> tuple[list[str], list[str]]:
    return [], []


_MODE_CONFIG_PROBLEMS: dict[str, Callable[[], tuple[list[str], list[str]]]] = {
    MODE_OIDC: _oidc_problems,
    MODE_BASIC: _basic_problems,
    MODE_ROTATING_FILE_BEARER: _rotating_problems,
    MODE_NONE: _no_problems,
}


def outbound_auth_configuration_status() -> dict[str, object]:
    """Redacted outbound-auth readiness; no secret is resolved."""
    mode = _auth_mode()
    missing, invalid = _MODE_CONFIG_PROBLEMS[mode]()
    return {
        "mode": mode,
        "ready": not missing and not invalid,
        "missing": tuple(sorted(missing)),
        "invalid": tuple(sorted(invalid)),
        "redacted": True,
    }


def _require_ready(mode: str) -> None:
    missing, invalid = _MODE_CONFIG_PROBLEMS[mode]()
    if missing or invalid:
        raise ChildAuthConfigurationError(f"outbound MCP {mode} identity is incomplete")


def read_rotating_bearer_token(path: Path) -> str:
    """Read the bearer from ``path`` fresh; it must be a non-empty 0600 file."""
    try:
        raw_mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChildAuthConfigurationError(
            "rotating bearer token file is unavailable"
        ) from exc
    if len(text.encode("utf-8", errors="replace")) > _MAX_BEARER_FILE_BYTES:
        raise ChildAuthConfigurationError("rotating bearer token file is too large")
    token = text.strip()
    if not token or any(char in token for char in "\r\n\x00 \t"):
        raise ChildAuthConfigurationError("rotating bearer token file is invalid")
    if stat.S_IMODE(raw_mode) & 0o077:
        raise ChildAuthConfigurationError(
            "rotating bearer token file must have file mode 0600"
        )
    return token


class HeaderAuth(httpx.Auth, httpx2.Auth):
    """Set ``Authorization`` per request; a 401 re-derives it once and retries.

    ``header`` is called for every request (off the event loop on the async
    path), so a rotated file or secret is picked up in band.
    """

    def __init__(self, header: Callable[[], str]) -> None:
        self._header = header

    def auth_flow(self, request: Any) -> Generator[Any, Any, None]:
        """The synchronous flow (both client libraries call it)."""
        return self.sync_auth_flow(request)

    def sync_auth_flow(self, request: Any) -> Generator[Any, Any, None]:
        """Authenticate one synchronous request."""
        request.headers["Authorization"] = self._header()
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = self._header()
            yield request

    async def async_auth_flow(self, request: Any) -> AsyncGenerator[Any, Any]:
        request.headers["Authorization"] = await anyio.to_thread.run_sync(self._header)
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = await anyio.to_thread.run_sync(
                self._header
            )
            yield request


def rotating_bearer_header(token_path: Path) -> str:
    """``Bearer`` re-read from the rotated token file."""
    return f"Bearer {read_rotating_bearer_token(token_path)}"


def basic_header(username: str, password_ref: str) -> str:
    """HTTP Basic whose password is resolved from its secret reference now."""
    password = resolve_secret_reference(password_ref)
    raw = f"{username}:{password}".encode()
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _oidc_auth() -> ClientCredentialsAuth:
    try:
        return client_credentials_auth(ClientCredentialsConfig.from_settings())
    except ValidationError as exc:
        raise ChildAuthConfigurationError(
            "outbound MCP OIDC identity is invalid"
        ) from exc


def _basic_auth() -> HeaderAuth:
    return HeaderAuth(
        functools.partial(
            basic_header,
            _text("MCP_BASIC_AUTH_USERNAME"),
            _text("MCP_BASIC_AUTH_PASSWORD_REF"),
        )
    )


def _rotating_auth() -> HeaderAuth:
    path = Path(_text("MCP_BEARER_TOKEN_FILE")).expanduser()
    return HeaderAuth(functools.partial(rotating_bearer_header, path))


_AUTH_BUILDERS: dict[str, Callable[[], Any]] = {
    MODE_OIDC: _oidc_auth,
    MODE_BASIC: _basic_auth,
    MODE_ROTATING_FILE_BEARER: _rotating_auth,
}

_OIDC_AUTH: ClientCredentialsAuth | None = None


def _has_authorization(headers: Mapping[str, Any] | None) -> bool:
    return bool(headers) and any(
        str(key).lower() == "authorization" for key in headers or {}
    )


def child_auth(headers: Mapping[str, Any] | None) -> Any | None:
    """The service auth for one remote child, or ``None`` when not applicable.

    The OIDC auth is process-shared so every child reuses one token cache.
    """
    global _OIDC_AUTH
    if _has_authorization(headers):
        return None
    mode = _auth_mode()
    if mode == MODE_NONE:
        return None
    _require_ready(mode)
    if mode != MODE_OIDC:
        return _AUTH_BUILDERS[mode]()
    if _OIDC_AUTH is None:
        _OIDC_AUTH = _oidc_auth()
    return _OIDC_AUTH


def service_session_max_age(headers: Mapping[str, Any] | None) -> float | None:
    """Seconds a service-authenticated OIDC child session may live, or ``None``.

    The session's result stream is authenticated once at connect, so it must be
    recycled before the minted bearer expires. Before the first mint the
    conservative assumed lifetime applies; no token is minted here. Basic and rotating-file modes
    re-derive the credential per request and need no forced recycle.
    """
    auth = child_auth(headers)
    if not isinstance(auth, ClientCredentialsAuth):
        return None
    ttl = auth.provider.access_token_ttl or _ASSUMED_TOKEN_TTL_S
    return max(_MIN_SESSION_MAX_AGE_S, ttl - _SESSION_EXPIRY_MARGIN_S)
