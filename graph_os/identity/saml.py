"""Native SAML 2.0 service provider (IDM-15; operator ruling 2026-09-24: SAML is
built natively in GraphOS, not only brokered through Keycloak).

Flow (SP-initiated only, HTTP-Redirect request / HTTP-POST response):

1. ``GET /auth/saml/{idp_id}/login`` issues an unsigned ``AuthnRequest`` with a
   fresh ``ID``, stores ``ID -> idp_id`` as a single-use record, and binds it to
   the browser with a ``SameSite=None`` cookie scoped to the ACS path (the IdP
   answers with a cross-site POST, which a ``Lax`` cookie would not reach).
2. ``POST /auth/saml/acs`` takes that record exactly once (the cookie names
   it), validates the response with :mod:`.saml_assertion` -- the assertion must
   answer THIS request (``InResponseTo``) -- and records the assertion ``ID`` in
   the replay cache until it expires. An unsolicited response has no record and
   is refused.
3. The verified ``NameID`` and the configured attributes go to the engine's
   ``external_login`` (:mod:`.idp_common`).

``GET /auth/saml/{idp_id}/metadata`` publishes this SP's metadata;
:func:`parse_idp_metadata` imports an IdP's (entity id, SSO URL, signing
certificates) for the administrator.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import time
import zlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from xml.sax.saxutils import escape, quoteattr

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from graph_os.identity.idp_common import (
    NO_STORE,
    ExternalAssertion,
    IdpDirectory,
    IdpRecord,
    LoginCompleter,
    OneShotStore,
    UnknownIdp,
)
from graph_os.identity.saml_assertion import (
    MAX_DOCUMENT_BYTES,
    NS,
    Expectation,
    SamlError,
    ValidatedAssertion,
    parse_document,
    validate_response,
)

__all__ = [
    "ACS_PATH",
    "TX_COOKIE",
    "SamlBroker",
    "SamlSettings",
    "authn_request",
    "parse_idp_metadata",
    "sp_metadata",
]

ACS_PATH = "/auth/saml/acs"
TX_COOKIE = "__Secure-graphos_saml_tx"
_TX_TTL_S = 600.0
_REDIRECT_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


def _https(value: str) -> str:
    if urlsplit(value).scheme != "https":
        raise ValueError("must be an https URL")
    return value


def _pem_body(value: str) -> str:
    body = "".join(
        line
        for line in value.strip().splitlines()
        if line and not line.startswith("-----")
    )
    try:
        base64.b64decode(body, validate=True)
    except binascii.Error:
        raise ValueError("not a base64 certificate") from None
    return body


class SamlSettings(BaseModel):
    """``IdpConfig.config_json`` of a ``kind=saml`` IdP."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idp_entity_id: str = Field(min_length=1, max_length=1024)
    idp_sso_url: str
    idp_certs: tuple[str, ...] = Field(min_length=1, max_length=2)
    sp_entity_id: str = Field(min_length=1, max_length=1024)
    acs_url: str
    name_id_format: str | None = None
    attribute_paths: dict[str, str] = Field(default_factory=dict)
    """``claim_path -> SAML attribute Name`` forwarded to the mapping rules."""
    username_attribute: str | None = None
    clock_skew_s: int = Field(default=60, ge=0, le=300)

    _urls = field_validator("idp_sso_url", "acs_url")(_https)

    @field_validator("idp_certs")
    @classmethod
    def _certs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_pem_body(cert) for cert in value)


def _pem(body: str) -> str:
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def authn_request(settings: SamlSettings, request_id: str, now: datetime) -> str:
    """The deflated, base64 ``SAMLRequest`` query value (HTTP-Redirect binding)."""
    policy = (
        f'<samlp:NameIDPolicy Format={quoteattr(settings.name_id_format)} AllowCreate="true"/>'
        if settings.name_id_format
        else '<samlp:NameIDPolicy AllowCreate="true"/>'
    )
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
        f'ID={quoteattr(request_id)} Version="2.0" IssueInstant="{_instant(now)}" '
        f"Destination={quoteattr(settings.idp_sso_url)} "
        f"AssertionConsumerServiceURL={quoteattr(settings.acs_url)} "
        f'ProtocolBinding="{_POST_BINDING}">'
        f"<saml:Issuer>{escape(settings.sp_entity_id)}</saml:Issuer>{policy}"
        "</samlp:AuthnRequest>"
    )
    compressor = zlib.compressobj(wbits=-15)
    deflated = compressor.compress(xml.encode()) + compressor.flush()
    return base64.b64encode(deflated).decode()


def sp_metadata(settings: SamlSettings) -> str:
    """This SP's ``EntityDescriptor``: POST ACS, signed assertions wanted."""
    name_id = (
        f"<md:NameIDFormat>{escape(settings.name_id_format)}</md:NameIDFormat>"
        if settings.name_id_format
        else ""
    )
    return (
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" '
        f"entityID={quoteattr(settings.sp_entity_id)}>"
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f"{name_id}"
        f'<md:AssertionConsumerService Binding="{_POST_BINDING}" '
        f'Location={quoteattr(settings.acs_url)} index="0" isDefault="true"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )


def parse_idp_metadata(xml: bytes) -> dict[str, Any]:
    """The SAML settings an IdP's metadata determines (hardened parse)."""
    try:
        root = parse_document(xml)
    except SamlError as exc:
        raise ValueError(str(exc)) from None
    descriptor = root.find("md:IDPSSODescriptor", NS)
    if root.tag != f"{{{NS['md']}}}EntityDescriptor" or descriptor is None:
        raise ValueError("not an IdP EntityDescriptor")
    sso = [
        s.get("Location")
        for s in descriptor.findall("md:SingleSignOnService", NS)
        if s.get("Binding") == _REDIRECT_BINDING
    ]
    certs = [
        "".join(c.itertext()).strip()
        for key in descriptor.findall("md:KeyDescriptor", NS)
        if key.get("use") in (None, "signing")
        for c in key.findall("ds:KeyInfo/ds:X509Data/ds:X509Certificate", NS)
    ]
    if not sso or not certs:
        raise ValueError("the IdP metadata lacks a Redirect SSO service or a signing key")
    return {"idp_entity_id": root.get("entityID"), "idp_sso_url": sso[0], "idp_certs": certs[:2]}


def _settings(record: IdpRecord) -> SamlSettings:
    try:
        return SamlSettings.model_validate(record.config)
    except ValidationError:
        raise UnknownIdp(record.idp_id) from None


def _login_error(code: str) -> Response:
    return RedirectResponse(f"/auth/login?error={code}", status_code=303, headers=NO_STORE)


def _decode_post(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_DOCUMENT_BYTES * 4 // 3 + 4:
        raise SamlError("SAMLResponse missing or too large")
    try:
        return base64.b64decode("".join(value.split()), validate=True)
    except binascii.Error:
        raise SamlError("SAMLResponse is not base64") from None


class SamlBroker:
    """Sign-in with every enabled SAML IdP; the engine decides who you are."""

    def __init__(
        self,
        *,
        directory: IdpDirectory,
        transactions: OneShotStore,
        replay: OneShotStore,
        completer: LoginCompleter,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._directory = directory
        self._transactions = transactions
        self._replay = replay
        self._completer = completer
        self._clock = clock

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    async def begin(self, request: Request) -> Response:
        """``GET /auth/saml/{idp_id}/login``: redirect with an AuthnRequest."""
        try:
            record = await self._directory.enabled(request.path_params["idp_id"], "saml")
            settings = _settings(record)
        except UnknownIdp:
            return _login_error("idp_unavailable")
        request_id = "_" + secrets.token_hex(20)
        self._transactions.put(request_id, {"idp_id": record.idp_id}, _TX_TTL_S)
        query = urlencode({"SAMLRequest": authn_request(settings, request_id, self._now())})
        response = RedirectResponse(
            f"{settings.idp_sso_url}?{query}", status_code=303, headers=NO_STORE
        )
        response.set_cookie(
            TX_COOKIE,
            request_id,
            max_age=int(_TX_TTL_S),
            path=ACS_PATH,
            secure=True,
            httponly=True,
            samesite="none",
        )
        return response

    def accept(
        self, xml: bytes, record: IdpRecord, request_id: str
    ) -> ValidatedAssertion:
        """Validate one response for a pending request and burn its assertion id."""
        settings = _settings(record)
        expect = Expectation(
            idp_entity_id=settings.idp_entity_id,
            sp_entity_id=settings.sp_entity_id,
            acs_url=settings.acs_url,
            request_id=request_id,
            now=self._now(),
            skew=timedelta(seconds=settings.clock_skew_s),
        )
        certs = tuple(_pem(body) for body in settings.idp_certs)
        validated = validate_response(xml, certs, expect)
        ttl = (validated.not_on_or_after - expect.now + expect.skew).total_seconds()
        replay_key = f"{record.idp_id}:{validated.assertion_id}"
        if not self._replay.first_sighting(replay_key, max(ttl, 1.0)):
            raise SamlError("assertion replayed")
        return validated

    def _assertion(self, record: IdpRecord, validated: ValidatedAssertion) -> ExternalAssertion:
        settings = _settings(record)
        claims = {
            path: list(validated.attributes[name])
            for path, name in settings.attribute_paths.items()
            if validated.attributes.get(name)
        }
        hint = validated.attributes.get(settings.username_attribute or "", ())
        return ExternalAssertion(
            idp_id=record.idp_id,
            subject=validated.subject,
            claims=claims,
            username_hint=hint[0] if hint else None,
        )

    async def acs(self, request: Request) -> Response:
        """``POST /auth/saml/acs``: the IdP's answer to OUR pending request."""
        request_id = request.cookies.get(TX_COOKIE)
        tx = self._transactions.take(request_id) if request_id else None
        if tx is None or request_id is None:
            return _login_error("stale_login")
        try:
            record = await self._directory.enabled(str(tx["idp_id"]), "saml")
            xml = _decode_post((await request.form()).get("SAMLResponse"))
            validated = self.accept(xml, record, request_id)
        except (UnknownIdp, SamlError):
            return _login_error("idp_unverified")
        response = await self._completer.complete(request, self._assertion(record, validated))
        response.delete_cookie(TX_COOKIE, path=ACS_PATH, secure=True, httponly=True)
        return response

    async def metadata(self, request: Request) -> Response:
        """``GET /auth/saml/{idp_id}/metadata``: this SP, as that IdP sees it."""
        try:
            record = await self._directory.enabled(request.path_params["idp_id"], "saml")
            settings = _settings(record)
        except UnknownIdp:
            return Response(status_code=404)
        return Response(sp_metadata(settings), media_type="application/samlmetadata+xml")

    def routes(self) -> list[Route]:
        return [
            Route("/auth/saml/{idp_id}/login", self.begin, methods=["GET"]),
            Route("/auth/saml/{idp_id}/metadata", self.metadata, methods=["GET"]),
            Route(ACS_PATH, self.acs, methods=["POST"]),
        ]
