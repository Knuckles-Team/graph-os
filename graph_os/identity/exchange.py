"""RFC 8693 token exchange at the local issuer (IDM-06).

A non-browser client trades a credential for a short-lived local-issuer
access token at ``POST /oauth/token``:

* ``urn:graph-os:token-type:api-key`` — an API key (``gok_…``); the token
  carries the key's CURRENT authority;
* ``urn:ietf:params:oauth:token-type:access_token`` / ``id_token`` — an
  upstream identity provider's token. It is verified by the registered
  :class:`UpstreamVerifier` for its issuer (the identity-provider brokers,
  IDM-12..15, register theirs), then the engine maps the subject to ONE
  principal (``external_login``: links, mapping rules, JIT policy). Upstream
  tokens are never forwarded as the principal token — they are exchanged.

An unregistered upstream issuer, an unknown token type and every failed
verification answer the same ``invalid_grant``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .admission import AdmissionService
from .broker import IdentityBroker
from .engine import IdentityCall, IdentityRefused, SignIn
from .material import client_ip_prefix, new_token

__all__ = [
    "API_KEY_TOKEN_TYPE",
    "TOKEN_EXCHANGE_GRANT",
    "UpstreamAssertion",
    "UpstreamVerifier",
    "exchange_routes",
]

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
API_KEY_TOKEN_TYPE = "urn:graph-os:token-type:api-key"
_UPSTREAM_TOKEN_TYPES = frozenset(
    {
        "urn:ietf:params:oauth:token-type:access_token",
        "urn:ietf:params:oauth:token-type:id_token",
    }
)
_ISSUED_TYPE = "urn:ietf:params:oauth:token-type:access_token"


@dataclass(frozen=True)
class UpstreamAssertion:
    """A verified upstream identity: which provider, which subject, which claims."""

    idp_id: str
    subject: str
    claims: Mapping[str, Sequence[str]] = field(default_factory=dict)
    username_hint: str | None = None


class UpstreamVerifier(Protocol):
    """Verifies one upstream provider's tokens (signature, issuer, audience, expiry)."""

    async def verify(self, token: str) -> UpstreamAssertion | None: ...


async def exchange_upstream(
    broker: IdentityBroker, assertion: UpstreamAssertion, ip_prefix: str | None
) -> str | None:
    """The engine's principal for ``assertion`` as a local token, or ``None``.

    The session ``external_login`` opens exists only for this exchange and is
    revoked before the token is returned.
    """
    session = new_token()
    request = {
        "idp_id": assertion.idp_id,
        "subject": assertion.subject,
        "claims": {name: list(values) for name, values in assertion.claims.items()},
        "session_token": session,
    }
    if assertion.username_hint:
        request["username_hint"] = assertion.username_hint
    if ip_prefix:
        request["ip_prefix"] = ip_prefix
    reply = await broker.engine.broker(
        IdentityCall("credential", "external_login", request)
    )
    if SignIn.parse(reply.expect("authenticate")).outcome != "ok":
        return None
    resolution = await broker.resolve_session(session)
    await broker.sign_out(session)
    if resolution is None or not resolution.usable:
        return None
    return broker.access_token(resolution, ("idp:" + assertion.idp_id,))


def _invalid(reason: str = "invalid_grant") -> Response:
    return JSONResponse(
        {"error": reason}, status_code=400, headers={"cache-control": "no-store"}
    )


class _Exchange:
    def __init__(
        self, admission: AdmissionService, upstream: Mapping[str, UpstreamVerifier]
    ) -> None:
        self._broker = admission.broker
        self._upstream = upstream

    async def _api_key_token(self, subject_token: str) -> str | None:
        resolution = await self._broker.verify_api_key(subject_token)
        if resolution is None or not resolution.usable:
            return None
        return self._broker.access_token(resolution, ("api_key",))

    async def _upstream_token(
        self, subject_token: str, ip_prefix: str | None
    ) -> str | None:
        for verifier in self._upstream.values():
            assertion = await verifier.verify(subject_token)
            if assertion is not None:
                return await exchange_upstream(self._broker, assertion, ip_prefix)
        return None

    async def _issue(
        self, request: Request, token_type: str, subject_token: str
    ) -> str | None:
        if token_type == API_KEY_TOKEN_TYPE:
            return await self._api_key_token(subject_token)
        if token_type in _UPSTREAM_TOKEN_TYPES:
            ip = client_ip_prefix(request.client.host if request.client else None)
            return await self._upstream_token(subject_token, ip)
        return None

    async def token(self, request: Request) -> Response:
        form = await request.form()
        if form.get("grant_type") != TOKEN_EXCHANGE_GRANT:
            return _invalid("unsupported_grant_type")
        subject_token = str(form.get("subject_token") or "")
        token_type = str(form.get("subject_token_type") or "")
        try:
            issued = await self._issue(request, token_type, subject_token)
        except (IdentityRefused, PermissionError):
            issued = None
        if issued is None:
            return _invalid()
        return JSONResponse(
            {
                "access_token": issued,
                "issued_token_type": _ISSUED_TYPE,
                "token_type": "Bearer",
                "expires_in": self._broker.issuer.settings.access_ttl_seconds,
            },
            headers={"cache-control": "no-store"},
        )


def exchange_routes(
    admission: AdmissionService, upstream: Mapping[str, UpstreamVerifier] | None = None
) -> list[Route]:
    exchange = _Exchange(admission, upstream or {})
    return [Route("/oauth/token", exchange.token, methods=["POST"])]
