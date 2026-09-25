"""Per-request admission: which principal a request carries (IDM-07/08/11).

Every served request is admitted once, before the unchanged agent-utilities
identity gate sees it. Admission turns whatever credential the request holds
into ONE local-issuer access token in the ``Authorization`` header:

* an API key (``Bearer gok_…``) → the key's CURRENT authority (its scopes
  intersected with its owner's, re-checked by the engine at every use);
* the session cookie → the live session's principal (revoked or expired ⇒ the
  cookie is cleared); a session still owing its second factor is refused;
* no credential in ``none`` mode → the bootstrap principal (the ``none``
  authenticator), through the very same token path;
* any other bearer passes through untouched and is verified downstream.

Cookie-authenticated state changes are refused without a same-origin
``Origin`` and the session's CSRF token; in ``none`` mode every request is
also refused unless its ``Host`` names loopback (DNS-rebinding guard).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .broker import IdentityBroker
from .browser import (
    clear_cookie_header,
    csrf_refusal,
    request_header,
    session_cookie_header,
    session_from_scope,
)
from .engine import IdentityRefused, Resolution
from .material import API_KEY_PREFIX
from .modes import NoneModeRequestGuard

__all__ = ["Admission", "AdmissionService", "BootstrapSession"]

_SESSION_COOKIE_MAX_AGE = 7 * 24 * 60 * 60
_UNINITIALIZED = "IDENTITY_NOT_INITIALIZED"


@dataclass(frozen=True)
class Admission:
    """What admission decided for one request."""

    #: A local-issuer access token to present downstream, if any.
    token: str | None = None
    #: A refusal ``(status, reason)``; the request goes no further.
    refusal: tuple[int, str] | None = None
    #: Response headers to add (session cookie set / cleared).
    set_headers: tuple[tuple[bytes, bytes], ...] = field(default_factory=tuple)
    #: Live, CSRF-admitted browser session's principal. Never set for a bearer.
    session_principal_id: str | None = None
    #: Broker-verified cookie token; downstream console adapter never reads raw cookies.
    session_token: str | None = None
    #: EG's session-bound MFA completion time, never a JWT auth_time.
    session_mfa_at_ms: int | None = None

    @classmethod
    def refuse(cls, status: int, reason: str) -> Admission:
        return cls(refusal=(status, reason))


@dataclass(frozen=True)
class BootstrapSession:
    """The shared ``none``-mode session and the bootstrap principal it resolves to."""

    session_token: str
    resolution: Resolution


def _api_key(authorization: str) -> str | None:
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential.startswith(API_KEY_PREFIX):
        return credential
    return None


class AdmissionService:
    """Resolve each request's credential through the broker."""

    def __init__(self, broker: IdentityBroker, guard: NoneModeRequestGuard) -> None:
        self._broker = broker
        self._guard = guard
        self._bootstrap: str | None = None
        self._bootstrap_lock = asyncio.Lock()

    @property
    def broker(self) -> IdentityBroker:
        return self._broker

    @property
    def none_guard(self) -> NoneModeRequestGuard:
        return self._guard

    async def mode(self) -> str | None:
        """The stored auth mode, or ``None`` while the store is uninitialized."""
        try:
            return await self._broker.mode()
        except IdentityRefused as refused:
            if refused.code == _UNINITIALIZED:
                return None
            raise

    async def admit(self, scope: Mapping[str, Any]) -> Admission:
        mode = await self.mode()
        if mode == "none":
            reason = self._guard.refusal(scope)
            if reason is not None:
                return Admission.refuse(403, reason)
        authorization = request_header(scope, b"authorization")
        if len(authorization) > 1:
            return Admission.refuse(401, "ambiguous_credentials")
        session = session_from_scope(scope)
        if authorization:
            if session is not None:
                return Admission.refuse(401, "ambiguous_credentials")
            return await self._admit_bearer(authorization[0])
        if session is not None:
            return await self._admit_session(scope, session, mode)
        if mode == "none":
            return await self._admit_bootstrap()
        return Admission()

    async def _admit_bearer(self, authorization: str) -> Admission:
        key = _api_key(authorization)
        if key is None:
            return Admission()
        resolution = await self._broker.verify_api_key(key)
        if resolution is None or not resolution.usable:
            return Admission.refuse(401, "api_key_invalid")
        return Admission(token=self._broker.access_token(resolution, ("api_key",)))

    async def _admit_session(
        self, scope: Mapping[str, Any], session: str, mode: str | None
    ) -> Admission:
        resolution = await self._broker.resolve_session(session)
        if resolution is None:
            if mode == "none":
                return await self._admit_bootstrap()
            return Admission(set_headers=(clear_cookie_header(),))
        if resolution.session_mfa_pending:
            return Admission.refuse(401, "second_factor_required")
        reason = csrf_refusal(scope, session)
        if reason is not None:
            return Admission.refuse(403, reason)
        mfa_at = getattr(resolution, "session_mfa_at_ms", None)
        if type(mfa_at) is not int or mfa_at < 0:
            mfa_at = None
        return Admission(
            token=self._token(resolution, mode),
            session_principal_id=resolution.principal_id,
            session_token=session,
            session_mfa_at_ms=mfa_at,
        )

    def _token(self, resolution: Resolution, mode: str | None) -> str:
        methods = ("none",) if mode == "none" else ("session",)
        return self._broker.access_token(resolution, methods)

    async def _admit_bootstrap(self) -> Admission:
        """``none`` mode: the bootstrap principal, one shared demo session."""
        bootstrap = await self.bootstrap()
        cookie = session_cookie_header(bootstrap.session_token, _SESSION_COOKIE_MAX_AGE)
        token = self._token(bootstrap.resolution, "none")
        return Admission(token=token, set_headers=(cookie,))

    async def bootstrap(self) -> BootstrapSession:
        """The ``none`` authenticator: always the bootstrap principal.

        One demo session is shared by every caller (they are all the same
        principal); it is re-opened when a transition revoked it.
        """
        async with self._bootstrap_lock:
            if self._bootstrap is not None:
                resolution = await self._broker.resolve_session(self._bootstrap)
                if resolution is not None:
                    return BootstrapSession(self._bootstrap, resolution)
            session = await self._broker.bootstrap_session()
            resolution = await self._broker.resolve_session(session)
            if resolution is None:
                raise PermissionError("the bootstrap session did not resolve")
            self._bootstrap = session
            return BootstrapSession(session, resolution)
