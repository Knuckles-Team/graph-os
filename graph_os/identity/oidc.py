"""OpenID Connect relying party for any number of identity providers (IDM-12).

Each enabled ``kind=oidc`` IdP in the engine's identity store is one relying
party: authorization code + PKCE S256, ``state`` bound to the browser and
single-use, ``nonce`` bound to the ID token, and a confidential or public
client. The ID token is the only assertion trusted:

* signature by a key from the IdP's JWKS (discovered from the issuer), with an
  asymmetric algorithm on the IdP's allowlist -- never ``none`` and never an
  HMAC algorithm (a public key is not an HMAC secret);
* ``iss`` equal to the configured issuer, ``aud`` containing this client (and
  ``azp`` equal to it when present), ``exp``/``iat``/``nbf`` within the IdP's
  clock skew, and ``nonce`` equal to the one stored for this ``state``.

The subject and the configured claim paths go to the engine's
``external_login``, which applies the IdP's mapping rules and JIT policy
(:mod:`.idp_common`). Logout revokes the engine session and, when the IdP
publishes an ``end_session_endpoint``, redirects there with ``id_token_hint``.

Threat model and the conformance corpus: ``REVIEW-HOTSPOTS.md`` (identity
lane) and ``tests/identity/test_oidc.py``.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode

import anyio
import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, KeySetSerialization
from joserfc.jws import extract_compact
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from graph_os.fleet.remote_oauth_broker import (
    OAuthDiscoveryError,
    _bounded_json_get,
    _bounded_token_post,
    _default_broker_http_client,
    _generate_pkce,
)
from graph_os.identity.idp_common import (
    NO_STORE,
    SESSION_COOKIE,
    ExternalAssertion,
    IdentityPort,
    IdentityRefused,
    IdpDirectory,
    IdpRecord,
    LoginCompleter,
    OneShotStore,
    SecretResolver,
    UnknownIdp,
    flatten_claims,
    identity_op,
    login_error,
    new_session_token,
    require_https,
)

__all__ = [
    "IDT_COOKIE",
    "TX_COOKIE",
    "IdTokenError",
    "OidcBroker",
    "OidcSettings",
    "ProviderCache",
    "ProviderMetadata",
    "parse_provider_metadata",
    "verify_access_token",
    "verify_id_token",
]

TX_COOKIE = "__Secure-graphos_oidc_tx"
"""The browser half of the login transaction: the ``state`` this browser began."""
IDT_COOKIE = "__Secure-graphos_oidc_idt"
"""``<idp_id>~<id_token>`` kept only for the logout ``id_token_hint``; the
path limits it to the logout route and no route accepts it as a credential."""

CALLBACK_PATH = "/auth/oidc/callback"
LOGOUT_PATH = "/auth/oidc/logout"
_TX_TTL_S = 600.0
_METADATA_TTL_S = 3600.0
_JWKS_TTL_S = 600.0
_JWKS_REFRESH_FLOOR_S = 60.0
_MAX_PARAM_CHARS = 2048
_ASYMMETRIC_ALGS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


class IdTokenError(ValueError):
    """The ID token failed a signature or claim check (never shown to a user)."""


class OidcSettings(BaseModel):
    """``IdpConfig.config_json`` of a ``kind=oidc`` IdP."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str
    client_id: str = Field(min_length=1, max_length=256)
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    claim_paths: tuple[str, ...] = (
        "groups",
        "email",
        "email_verified",
        "preferred_username",
    )
    group_paths: tuple[str, ...] = ("groups",)
    username_claim: str = "preferred_username"
    signing_algs: tuple[str, ...] = ("RS256", "PS256", "ES256")
    clock_skew_s: int = Field(default=60, ge=0, le=300)
    post_logout_redirect_uri: str | None = None

    _https = field_validator("issuer", "redirect_uri")(require_https)

    @field_validator("post_logout_redirect_uri")
    @classmethod
    def _optional_https(cls, value: str | None) -> str | None:
        return None if value is None else require_https(value)

    @field_validator("scopes")
    @classmethod
    def _openid_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if "openid" not in value:
            raise ValueError("an OIDC sign-in must request the openid scope")
        return value

    @field_validator("signing_algs")
    @classmethod
    def _asymmetric_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or not set(value) <= _ASYMMETRIC_ALGS:
            raise ValueError("ID tokens are verified with asymmetric algorithms only")
        return value


@dataclass(frozen=True)
class ProviderMetadata:
    """The discovered endpoints of one issuer."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None


def _endpoint(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise OAuthDiscoveryError(f"provider metadata lacks {name}")
    try:
        return require_https(value)
    except ValueError:
        raise OAuthDiscoveryError(f"provider {name} must be https") from None


def parse_provider_metadata(
    payload: Mapping[str, Any], expected_issuer: str
) -> ProviderMetadata:
    """Validate an OpenID Provider configuration document.

    The ``issuer`` must equal the configured issuer EXACTLY (OIDC Discovery
    §4.3), every endpoint must be https, and a provider that lists its PKCE
    methods must list ``S256``.
    """
    if payload.get("issuer") != expected_issuer:
        raise OAuthDiscoveryError(
            "provider issuer does not match the configured issuer"
        )
    methods = payload.get("code_challenge_methods_supported")
    if methods is not None and "S256" not in methods:
        raise OAuthDiscoveryError("provider does not support PKCE S256")
    end_session = payload.get("end_session_endpoint")
    return ProviderMetadata(
        issuer=expected_issuer,
        authorization_endpoint=_endpoint(payload, "authorization_endpoint"),
        token_endpoint=_endpoint(payload, "token_endpoint"),
        jwks_uri=_endpoint(payload, "jwks_uri"),
        end_session_endpoint=(
            _endpoint(payload, "end_session_endpoint") if end_session else None
        ),
    )


def _signing_keys(document: Mapping[str, Any]) -> KeySet:
    """The JWKS's signature keys; encryption keys are not ID-token signers."""
    raw = document.get("keys")
    if not isinstance(raw, list):
        raise OAuthDiscoveryError("the JWKS has no keys array")
    keys = [k for k in raw if isinstance(k, dict) and k.get("use", "sig") == "sig"]
    try:
        return KeySet.import_key_set(cast(KeySetSerialization, {"keys": keys}))
    except (JoseError, ValueError):
        raise OAuthDiscoveryError("the JWKS holds an unusable key") from None


def _discovery_url(issuer: str) -> str:
    return issuer.rstrip("/") + "/.well-known/openid-configuration"


class ProviderCache:
    """Discovery documents and JWKS per issuer, fetched over bounded HTTPS.

    A JWKS is refetched when it is older than its TTL, or when a token names an
    unknown ``kid`` (key rotation) -- at most once per refresh floor, so a
    stream of forged ``kid`` values cannot turn into a fetch storm.
    """

    def __init__(
        self,
        http_client_factory: Callable[[], httpx.Client] = _default_broker_http_client,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http_client_factory
        self._clock = clock
        self._metadata: dict[str, tuple[float, ProviderMetadata]] = {}
        self._jwks: dict[str, tuple[float, KeySet]] = {}

    def _get_json(self, url: str) -> dict[str, Any]:
        with self._http() as client:
            return _bounded_json_get(client, url)

    def metadata(self, issuer: str) -> ProviderMetadata:
        cached = self._metadata.get(issuer)
        if cached and self._clock() - cached[0] < _METADATA_TTL_S:
            return cached[1]
        parsed = parse_provider_metadata(self._get_json(_discovery_url(issuer)), issuer)
        self._metadata[issuer] = (self._clock(), parsed)
        return parsed

    def keys(self, metadata: ProviderMetadata, kid: str | None) -> KeySet:
        cached = self._jwks.get(metadata.jwks_uri)
        age = self._clock() - cached[0] if cached else None
        if cached and age is not None and age < _JWKS_TTL_S:
            known = kid is None or any(key.kid == kid for key in cached[1].keys)
            if known or age < _JWKS_REFRESH_FLOOR_S:
                return cached[1]
        keyset = _signing_keys(self._get_json(metadata.jwks_uri))
        self._jwks[metadata.jwks_uri] = (self._clock(), keyset)
        return keyset

    def exchange_code(
        self, token_endpoint: str, form: dict[str, str]
    ) -> dict[str, Any]:
        with self._http() as client:
            return _bounded_token_post(client, token_endpoint, form)


# ---------------------------------------------------------------------------
# ID token verification
# ---------------------------------------------------------------------------
def _check_audience(claims: Mapping[str, Any], client_id: str) -> None:
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    if not isinstance(audiences, list) or client_id not in audiences:
        raise IdTokenError("aud does not include this client")
    azp = claims.get("azp")
    if (azp is not None or len(audiences) > 1) and azp != client_id:
        raise IdTokenError("azp is not this client")


def _registry(settings: OidcSettings, nonce: str, now: int) -> jwt.JWTClaimsRegistry:
    return jwt.JWTClaimsRegistry(
        now=now,
        leeway=settings.clock_skew_s,
        iss={"essential": True, "value": settings.issuer},
        sub={"essential": True},
        exp={"essential": True},
        iat={"essential": True},
        nonce={"essential": True, "value": nonce},
    )


def verify_id_token(
    id_token: str, *, settings: OidcSettings, keys: KeySet, nonce: str, now: int
) -> dict[str, Any]:
    """The claims of a valid ID token for this client and login transaction."""
    if id_token.count(".") != 2:
        raise IdTokenError("an ID token must be a compact JWS")
    try:
        token = jwt.decode(id_token, keys, algorithms=list(settings.signing_algs))
        _registry(settings, nonce, now).validate(token.claims)
    except (JoseError, ValueError) as exc:
        raise IdTokenError(type(exc).__name__) from None
    claims: dict[str, Any] = dict(token.claims)
    _check_audience(claims, settings.client_id)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise IdTokenError("sub must be a non-empty string of at most 255 characters")
    return claims


def verify_access_token(
    access_token: str, *, settings: OidcSettings, keys: KeySet, now: int
) -> dict[str, Any]:
    """Verify an RFC 9068 JWT access token for this configured client.

    ID tokens have a different purpose and require the browser transaction's
    nonce. Only a signed ``at+jwt`` access token can use this exchange path.
    """
    if access_token.count(".") != 2:
        raise IdTokenError("an access token must be a compact JWS")
    try:
        header = extract_compact(access_token.encode()).protected
        if header.get("typ") != "at+jwt":
            raise IdTokenError("token is not an at+jwt access token")
        decoded = jwt.decode(access_token, keys, algorithms=list(settings.signing_algs))
        jwt.JWTClaimsRegistry(
            now=now,
            leeway=settings.clock_skew_s,
            iss={"essential": True, "value": settings.issuer},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
        ).validate(decoded.claims)
    except (JoseError, ValueError) as exc:
        raise IdTokenError(type(exc).__name__) from None
    claims: dict[str, Any] = dict(decoded.claims)
    _check_audience(claims, settings.client_id)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise IdTokenError("sub must be a non-empty string of at most 255 characters")
    return claims


def _unverified_issuer(token: str) -> str | None:
    """Select a configured provider; the later signature check establishes trust."""
    if len(token) > 24 * 1024 or token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "==="))
    except (binascii.Error, ValueError):
        return None
    issuer = claims.get("iss") if isinstance(claims, dict) else None
    return issuer if isinstance(issuer, str) else None


def _unverified_kid(id_token: str) -> str | None:
    try:
        header = extract_compact(id_token.encode()).protected
    except (JoseError, ValueError):
        raise IdTokenError("malformed ID token") from None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------
def _settings(record: IdpRecord) -> OidcSettings:
    try:
        return OidcSettings.model_validate(record.config)
    except ValidationError:
        raise UnknownIdp(record.idp_id) from None


def _query_param(request: Request, name: str) -> str | None:
    value = request.query_params.get(name)
    if value is None or not value or len(value) > _MAX_PARAM_CHARS:
        return None
    return value


def _sets_session(response: Response) -> bool:
    prefix = f"{SESSION_COOKIE}="
    return any(
        value.startswith(prefix)
        for key, value in ((k.decode(), v.decode()) for k, v in response.raw_headers)
        if key == "set-cookie"
    )


class OidcBroker:
    """Sign-in with every enabled OIDC IdP; the engine decides who you are."""

    def __init__(
        self,
        *,
        port: IdentityPort,
        directory: IdpDirectory,
        transactions: OneShotStore,
        completer: LoginCompleter,
        secrets: SecretResolver,
        providers: ProviderCache | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._port = port
        self._directory = directory
        self._transactions = transactions
        self._completer = completer
        self._secrets = secrets
        self._providers = providers or ProviderCache()
        self._clock = clock

    async def _provider(
        self, record: IdpRecord
    ) -> tuple[OidcSettings, ProviderMetadata]:
        settings = _settings(record)
        metadata = await anyio.to_thread.run_sync(
            self._providers.metadata, settings.issuer
        )
        return settings, metadata

    async def verify_exchange_access_token(
        self, token: str
    ) -> ExternalAssertion | None:
        """Verify one access JWT against an enabled engine IdP, or refuse it."""
        issuer = _unverified_issuer(token)
        if issuer is None:
            return None
        matches: list[tuple[IdpRecord, OidcSettings]] = []
        for record in await self._directory.records():
            if record.kind != "oidc" or not record.enabled:
                continue
            try:
                settings = _settings(record)
            except UnknownIdp:
                return None
            if settings.issuer == issuer:
                matches.append((record, settings))
        # Two enabled records for one issuer would make the authority mapping
        # ambiguous even when they share a JWKS. No ordering rule may choose it.
        if len(matches) != 1:
            return None
        record, settings = matches[0]
        try:
            _, metadata = await self._provider(record)
            keys = await anyio.to_thread.run_sync(
                self._providers.keys, metadata, _unverified_kid(token)
            )
            claims = verify_access_token(
                token, settings=settings, keys=keys, now=int(self._clock())
            )
        except (IdTokenError, OAuthDiscoveryError, UnknownIdp, httpx.HTTPError):
            return None
        username = claims.get(settings.username_claim)
        return ExternalAssertion(
            idp_id=record.idp_id,
            subject=claims["sub"],
            claims=flatten_claims(
                claims, settings.claim_paths, group_paths=settings.group_paths
            ),
            username_hint=username if isinstance(username, str) else None,
        )

    async def begin(self, request: Request) -> Response:
        """``GET /auth/oidc/{idp_id}/login``: redirect to the IdP."""
        try:
            record = await self._directory.enabled(
                request.path_params["idp_id"], "oidc"
            )
            settings, metadata = await self._provider(record)
        except (UnknownIdp, OAuthDiscoveryError):
            return login_error("idp_unavailable")
        state, nonce = new_session_token(), new_session_token()
        verifier, challenge = _generate_pkce()
        tx = {"idp_id": record.idp_id, "nonce": nonce, "verifier": verifier}
        self._transactions.put(state, tx, _TX_TTL_S)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.client_id,
                "redirect_uri": settings.redirect_uri,
                "scope": " ".join(settings.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        response = RedirectResponse(
            f"{metadata.authorization_endpoint}?{query}",
            status_code=303,
            headers=NO_STORE,
        )
        response.set_cookie(
            TX_COOKIE,
            state,
            max_age=int(_TX_TTL_S),
            path=CALLBACK_PATH,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    def _claim_transaction(self, request: Request) -> tuple[str, dict[str, Any]] | None:
        state = _query_param(request, "state")
        code = _query_param(request, "code")
        if state is None or code is None or request.cookies.get(TX_COOKIE) != state:
            return None
        tx = self._transactions.take(state)
        return (code, tx) if tx is not None else None

    def _token_form(
        self, record: IdpRecord, settings: OidcSettings, code: str, verifier: str
    ) -> dict[str, str]:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
            "client_id": settings.client_id,
            "code_verifier": verifier,
        }
        if record.secret_ref:
            secret = self._secrets.get(record.secret_ref)
            if not secret:
                raise OAuthDiscoveryError("the client secret is not provisioned")
            form["client_secret"] = secret
        return form

    def _verify(
        self,
        metadata: ProviderMetadata,
        settings: OidcSettings,
        id_token: str,
        nonce: str,
    ) -> dict[str, Any]:
        keys = self._providers.keys(metadata, _unverified_kid(id_token))
        now = int(self._clock())
        return verify_id_token(
            id_token, settings=settings, keys=keys, nonce=nonce, now=now
        )

    async def _assert(
        self, code: str, tx: Mapping[str, Any]
    ) -> tuple[ExternalAssertion, str]:
        record = await self._directory.enabled(str(tx["idp_id"]), "oidc")
        settings, metadata = await self._provider(record)
        form = self._token_form(record, settings, code, str(tx["verifier"]))
        tokens = await anyio.to_thread.run_sync(
            self._providers.exchange_code, metadata.token_endpoint, form
        )
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise IdTokenError("the token response carries no ID token")
        claims = await anyio.to_thread.run_sync(
            self._verify, metadata, settings, id_token, str(tx["nonce"])
        )
        username = claims.get(settings.username_claim)
        assertion = ExternalAssertion(
            idp_id=record.idp_id,
            subject=str(claims["sub"]),
            claims=flatten_claims(
                claims, settings.claim_paths, group_paths=settings.group_paths
            ),
            username_hint=username if isinstance(username, str) else None,
        )
        return assertion, id_token

    async def callback(self, request: Request) -> Response:
        """``GET /auth/oidc/callback``: verify, then hand the engine the subject."""
        if request.query_params.get("error"):
            return login_error("idp_refused")
        claimed = self._claim_transaction(request)
        if claimed is None:
            return login_error("stale_login")
        try:
            assertion, id_token = await self._assert(*claimed)
        except (UnknownIdp, OAuthDiscoveryError, IdTokenError):
            return login_error("idp_unverified")
        response = await self._completer.complete(request, assertion)
        response.delete_cookie(
            TX_COOKIE, path=CALLBACK_PATH, secure=True, httponly=True
        )
        if _sets_session(response):
            response.set_cookie(
                IDT_COOKIE,
                f"{assertion.idp_id}~{id_token}",
                path=LOGOUT_PATH,
                secure=True,
                httponly=True,
                samesite="lax",
            )
        return response

    async def _end_session_url(self, hint: str | None) -> str | None:
        idp_id, _, id_token = (hint or "").partition("~")
        if not id_token:
            return None
        try:
            settings, metadata = await self._provider(
                await self._directory.enabled(idp_id, "oidc")
            )
        except (UnknownIdp, OAuthDiscoveryError):
            return None
        if metadata.end_session_endpoint is None:
            return None
        params = {"id_token_hint": id_token, "client_id": settings.client_id}
        if settings.post_logout_redirect_uri:
            params["post_logout_redirect_uri"] = settings.post_logout_redirect_uri
        return f"{metadata.end_session_endpoint}?{urlencode(params)}"

    async def logout(self, request: Request) -> Response:
        """``POST /auth/oidc/logout``: revoke the engine session, then RP-initiated
        logout at the IdP with ``id_token_hint``."""
        session = request.cookies.get(SESSION_COOKIE)
        if session:
            op = identity_op("session", "revoke", {"session_token": session})
            try:
                await self._port.call(op)
            except IdentityRefused:
                pass
        target = await self._end_session_url(request.cookies.get(IDT_COOKIE))
        response = RedirectResponse(
            target or "/auth/login", status_code=303, headers=NO_STORE
        )
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
        response.delete_cookie(IDT_COOKIE, path=LOGOUT_PATH, secure=True, httponly=True)
        return response

    def routes(self) -> list[Route]:
        return [
            Route("/auth/oidc/{idp_id}/login", self.begin, methods=["GET"]),
            Route(CALLBACK_PATH, self.callback, methods=["GET"]),
            Route(LOGOUT_PATH, self.logout, methods=["POST"]),
        ]
