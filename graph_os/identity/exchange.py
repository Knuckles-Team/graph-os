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
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .admission import AdmissionService
from .broker import IdentityBroker
from .engine import IdentityCall, IdentityRefused, SignIn
from .idp_common import ExternalAssertion
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
_MAX_FORM_BYTES = 32 * 1024
_MAX_SUBJECT_TOKEN_BYTES = 24 * 1024
_MAX_FORM_FIELDS = 8
_FORM_FIELDS = frozenset({"grant_type", "subject_token", "subject_token_type"})


@dataclass(frozen=True)
class UpstreamAssertion:
    """A verified upstream identity bound to one provider and local tenant."""

    idp_id: str
    tenant_id: str
    subject: str
    claims: Mapping[str, Sequence[str]] = field(default_factory=dict)
    username_hint: str | None = None


class UpstreamVerifier(Protocol):
    """Verify one provider; bind its tenant from trusted config or verified claims."""

    async def verify(self, token: str) -> UpstreamAssertion | None: ...


class OidcAccessTokenSource(Protocol):
    """The existing OIDC broker's engine-configured access-token verifier."""

    async def verify_exchange_access_token(
        self, token: str
    ) -> ExternalAssertion | None: ...


class ConfiguredOidcVerifier:
    """Bind a verified OIDC access JWT to the deployment's one tenant."""

    def __init__(self, source: OidcAccessTokenSource, tenant_id: str) -> None:
        self._source = source
        self._tenant_id = tenant_id

    async def verify(self, token: str) -> UpstreamAssertion | None:
        assertion = await self._source.verify_exchange_access_token(token)
        if assertion is None:
            return None
        return UpstreamAssertion(
            assertion.idp_id,
            self._tenant_id,
            assertion.subject,
            assertion.claims,
            assertion.username_hint,
        )


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
    outcome = SignIn.parse(reply.expect("authenticate")).outcome
    if outcome == "mfa_required":
        # The engine opened a pending session, but token exchange cannot
        # complete an interactive second factor. Do not leave it live.
        await broker.sign_out(session)
        return None
    if outcome != "ok":
        return None
    try:
        resolution = await broker.resolve_session(session)
    finally:
        await broker.sign_out(session)
    if resolution is None or not resolution.usable:
        return None
    return broker.access_token(resolution, ("idp:" + assertion.idp_id,))


def _invalid(reason: str = "invalid_grant") -> Response:
    return JSONResponse(
        {"error": reason}, status_code=400, headers={"cache-control": "no-store"}
    )


async def _exchange_form(request: Request) -> dict[str, str] | None:
    """Read one bounded URL-encoded grant without accepting duplicate fields."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if media_type.lower() != "application/x-www-form-urlencoded":
        return None
    if request.headers.get("content-length", "").isdigit():
        if int(request.headers["content-length"]) > _MAX_FORM_BYTES:
            return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_FORM_BYTES:
            return None
        body.extend(chunk)
    try:
        values = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=_MAX_FORM_FIELDS,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeError, ValueError):
        return None
    if set(values) != _FORM_FIELDS or any(len(items) != 1 for items in values.values()):
        return None
    form = {key: items[0] for key, items in values.items()}
    if len(form["subject_token"].encode("utf-8")) > _MAX_SUBJECT_TOKEN_BYTES:
        return None
    return form


class _Exchange:
    def __init__(
        self,
        admission: AdmissionService,
        upstream: Mapping[str, UpstreamVerifier],
        default_verifier: UpstreamVerifier | None = None,
    ) -> None:
        self._broker = admission.broker
        self._upstream = upstream
        self._default_verifier = default_verifier

    async def _api_key_token(self, subject_token: str) -> str | None:
        resolution = await self._broker.verify_api_key(subject_token)
        if resolution is None or not resolution.usable:
            return None
        return self._broker.access_token(resolution, ("api_key",))

    async def _upstream_token(
        self, subject_token: str, ip_prefix: str | None, token_type: str = ""
    ) -> str | None:
        matches: list[UpstreamAssertion] = []
        for idp_id, verifier in self._upstream.items():
            assertion = await verifier.verify(subject_token)
            if assertion is not None:
                if (
                    assertion.idp_id != idp_id
                    or assertion.tenant_id != self._broker.issuer.settings.tenant
                    or not assertion.subject
                ):
                    return None
                matches.append(assertion)
        if (
            token_type == "urn:ietf:params:oauth:token-type:access_token"
            and self._default_verifier is not None
        ):
            assertion = await self._default_verifier.verify(subject_token)
            if (
                assertion is not None
                and assertion.tenant_id == self._broker.issuer.settings.tenant
                and assertion.subject
            ):
                matches.append(assertion)
            elif assertion is not None:
                return None
        if len(matches) != 1:
            return None
        return await exchange_upstream(self._broker, matches[0], ip_prefix)

    async def _issue(
        self, request: Request, token_type: str, subject_token: str
    ) -> str | None:
        if token_type == API_KEY_TOKEN_TYPE:
            return await self._api_key_token(subject_token)
        if token_type in _UPSTREAM_TOKEN_TYPES:
            ip = client_ip_prefix(request.client.host if request.client else None)
            return await self._upstream_token(subject_token, ip, token_type)
        return None

    async def token(self, request: Request) -> Response:
        form = await _exchange_form(request)
        if form is None:
            return _invalid("invalid_request")
        if form.get("grant_type") != TOKEN_EXCHANGE_GRANT:
            return _invalid("unsupported_grant_type")
        subject_token = form["subject_token"]
        token_type = form["subject_token_type"]
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
    admission: AdmissionService,
    upstream: Mapping[str, UpstreamVerifier] | None = None,
    default_verifier: UpstreamVerifier | None = None,
) -> list[Route]:
    exchange = _Exchange(admission, upstream or {}, default_verifier)
    return [Route("/oauth/token", exchange.token, methods=["POST"])]
