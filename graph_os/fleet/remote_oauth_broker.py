"""Remote browser-OAuth broker for per-principal MCP child credentials.

RF-ADR-009 assigns authenticated MCP child admission to ``graph_os.fleet``. This
module owns the one credential class that admission needs for OAuth-gated
remote children: a per-user, per-provider, per-resource token minted through a
browser-mediated authorization flow and held by the GraphOS server, never by a
shared service identity.

* Provider registration (:class:`ProviderDescriptor`, :class:`ProviderRegistry`)
  is administrator-populated only; callers never supply provider identity.
* RFC 9728 and RFC 8414 discovery is HTTPS-only, bounded and issuer-consistent.
* Clients are pre-registered public PKCE clients or RFC 7591 dynamic
  registrations cached per provider.
* Authorization transactions are encrypted, session-bound, single-use and
  short-lived; callbacks reject redirect, principal and scope drift.
* Tokens are stored encrypted per tenant, principal, provider, resource and
  audience; refresh is serialized per token and revocation writes a
  fail-closed tombstone.
* :meth:`RemoteOAuthBroker.bearer_headers_for` is the only forwarding seam. It
  forwards only to the provider's exact registered resource and treats an
  expired grant as absent.
* Audit records carry pseudonymous tenant/principal references and never a
  code, state, verifier or token value.

An OAuth-gated child is served over an ephemeral per-request session only; it
is never pool-mounted or cached in the shared probe cache, so a per-user token
never reaches the principal-agnostic child pool.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import secrets
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from agent_utilities.security.brain_context import ActorContext
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("graph_os.fleet.remote_oauth_broker.audit")

__all__ = [
    "AuthorizationServerMetadata",
    "DynamicClientRegistrar",
    "OAuthBindingError",
    "OAuthGrantBinding",
    "OAuthDiscoveryError",
    "OAuthProviderError",
    "OAuthRedirectNotAllowlistedError",
    "OAuthRefreshRaceError",
    "OAuthRevokedError",
    "OAuthScopeError",
    "OAuthStateError",
    "OAuthTokenAbsentError",
    "OAuthTransaction",
    "ProtectedResourceMetadata",
    "ProviderDescriptor",
    "ProviderRegistry",
    "RemoteOAuthBroker",
    "Rfc7591DynamicClientRegistrar",
    "StoredToken",
    "TransactionStore",
    "OAuthTokenStore",
    "discover_authorization_server",
    "discover_protected_resource",
]

# ---------------------------------------------------------------------------
# Bounds — explicit, numeric, never open-ended (lane contract: "Algorithmic
# and resource budget").
# ---------------------------------------------------------------------------
_MAX_DISCOVERY_RESPONSE_BYTES = 256 * 1024
_DISCOVERY_TIMEOUT_S = 10.0
_MAX_TOKEN_RESPONSE_BYTES = 1024 * 1024
_TOKEN_EXCHANGE_TIMEOUT_S = 20.0
_TRANSACTION_TTL_S = 300.0
_MAX_STATE_LEN = 256
_CONSUMED_TOMBSTONE = "__consumed__"


# ---------------------------------------------------------------------------
# Errors — one exception type per fail-closed boundary, never a bare bool.
# ---------------------------------------------------------------------------
class OAuthProviderError(RuntimeError):
    """Unknown, disabled, or otherwise rejected provider registration."""


class OAuthDiscoveryError(RuntimeError):
    """RFC 9728 / RFC 8414 discovery failed, was malformed, or was inconsistent."""


class OAuthStateError(RuntimeError):
    """The transaction ``state`` was missing, unknown, replayed, or expired."""


class OAuthBindingError(RuntimeError):
    """The callback did not bind to the transaction's principal/tenant/session."""


class OAuthRedirectNotAllowlistedError(RuntimeError):
    """The requested redirect URI is not the provider's exact registered value."""


class OAuthScopeError(RuntimeError):
    """The authorization server granted a scope outside what was requested."""


class OAuthTokenAbsentError(RuntimeError):
    """No stored token/refresh-token exists for this principal/provider/resource."""


class OAuthRevokedError(RuntimeError):
    """The stored record is revoked (or its revocation status is ambiguous)."""


class OAuthRefreshRaceError(RuntimeError):
    """The stored record changed underneath a refresh; retry."""


# ---------------------------------------------------------------------------
# 1. Provider registration — administrator-approved only.
# ---------------------------------------------------------------------------
class ProviderDescriptor(BaseModel):
    """One administrator-approved remote-MCP OAuth provider.

    Never constructed from caller-supplied request data — a
    :class:`ProviderRegistry` is populated only by deployment/administration code.
    Abstraction seam for "abstraction-first, no opinionation": nothing here is
    specific to one vendor's quirks; a provider is fully described by this model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=128)
    resource_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Exact remote MCP resource URL. Never a caller-supplied value.",
    )
    authorization_server_url: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "Explicit authorization-server issuer to use when the protected-resource "
            "metadata lists more than one. Unset picks the resource metadata's first "
            "entry."
        ),
    )
    client_id: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Pre-registered public PKCE client id for the deployment callback. "
            "Mutually exclusive with dynamic_client_registration=True — exactly "
            "one client-identity source is configured, never both."
        ),
    )
    dynamic_client_registration: bool = Field(
        default=False,
        description=(
            "RFC 7591 opt-in: when true, client_id must be unset and is instead "
            "resolved once via Rfc7591DynamicClientRegistrar (or an injected "
            "DynamicClientRegistrar) and cached idempotently per provider_id."
        ),
    )
    redirect_uri: str = Field(
        min_length=1,
        max_length=2048,
        description="Exact deployment callback route. The sole allowlisted redirect.",
    )
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    enabled: bool = Field(
        default=False,
        description="Staged rollout gate — disabled providers reject begin()/callback().",
    )

    @field_validator("resource_url", "redirect_uri")
    @classmethod
    def _require_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("must be an exact HTTPS URL without embedded credentials")
        return value

    @field_validator("authorization_server_url")
    @classmethod
    def _optional_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls._require_https(value)

    @field_validator("client_id")
    @classmethod
    def _client_id_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("client_id must not be blank")
        return value

    @model_validator(mode="after")
    def _require_exactly_one_client_identity_source(self) -> ProviderDescriptor:
        if self.dynamic_client_registration and self.client_id:
            raise ValueError(
                "dynamic_client_registration and a pre-registered client_id are "
                "mutually exclusive"
            )
        if not self.dynamic_client_registration and not self.client_id:
            raise ValueError(
                "client_id is required unless dynamic_client_registration is enabled"
            )
        return self


class ProviderRegistry:
    """Administrator-populated provider set. No request-time registration exists."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}

    def register(self, descriptor: ProviderDescriptor) -> None:
        """Administrator-only call — never reachable from an MCP tool argument."""
        self._providers[descriptor.provider_id] = descriptor

    def get(self, provider_id: str) -> ProviderDescriptor | None:
        return self._providers.get(provider_id)

    def require_enabled(self, provider_id: str) -> ProviderDescriptor:
        provider = self._providers.get(str(provider_id or ""))
        if provider is None or not provider.enabled:
            raise OAuthProviderError("unknown or disabled provider")
        return provider

    def enabled_providers(self) -> tuple[ProviderDescriptor, ...]:
        """Return the administrator-populated enabled set for broker reads."""

        return tuple(
            sorted(
                (provider for provider in self._providers.values() if provider.enabled),
                key=lambda provider: provider.provider_id,
            )
        )


# ---------------------------------------------------------------------------
# 2. Discovery — RFC 9728 protected-resource, RFC 8414 authorization-server.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProtectedResourceMetadata:
    resource: str
    authorization_servers: tuple[str, ...]


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    code_challenge_methods_supported: tuple[str, ...]
    grant_types_supported: tuple[str, ...]
    registration_endpoint: str | None = None
    """RFC 7591 §2 dynamic-client-registration endpoint, when advertised. HTTPS-
    validated the same as every other discovered endpoint; ``None`` when the
    authorization server does not support DCR (a provider that also has no
    pre-registered ``client_id`` then fails closed at registration time, not
    silently)."""


def _bounded_json_get(client: httpx.Client, url: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise OAuthDiscoveryError("discovery endpoint must be an exact HTTPS URL")
    try:
        with client.stream("GET", url, headers={"Accept": "application/json"}) as resp:
            resp.raise_for_status()
            body = bytearray()
            for chunk in resp.iter_bytes(65536):
                body.extend(chunk)
                if len(body) > _MAX_DISCOVERY_RESPONSE_BYTES:
                    raise OAuthDiscoveryError("discovery response exceeded its bound")
    except httpx.HTTPError as exc:
        raise OAuthDiscoveryError("discovery request failed") from exc
    try:
        payload = json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError):
        raise OAuthDiscoveryError("discovery response was not valid JSON") from None
    if not isinstance(payload, dict):
        raise OAuthDiscoveryError("discovery response had an unexpected shape")
    return payload


def _protected_resource_metadata_url(resource_url: str) -> str:
    """RFC 9728 §3.1 well-known URI construction."""
    parsed = urlsplit(resource_url)
    path = parsed.path.rstrip("/")
    well_known = f"/.well-known/oauth-protected-resource{path}"
    return f"{parsed.scheme}://{parsed.netloc}{well_known}"


def _authorization_server_metadata_url(issuer: str) -> str:
    """RFC 8414 §3.1 well-known URI construction (insert before the issuer's path)."""
    parsed = urlsplit(issuer)
    path = parsed.path.rstrip("/")
    well_known = f"/.well-known/oauth-authorization-server{path}"
    return f"{parsed.scheme}://{parsed.netloc}{well_known}"


def discover_protected_resource(
    client: httpx.Client, resource_url: str
) -> ProtectedResourceMetadata:
    """RFC 9728: fetch the protected-resource metadata for ``resource_url``."""
    metadata_url = _protected_resource_metadata_url(resource_url)
    payload = _bounded_json_get(client, metadata_url)
    resource = payload.get("resource")
    servers = payload.get("authorization_servers")
    if (
        not isinstance(resource, str)
        or not resource
        or not isinstance(servers, list)
        or not servers
        or not all(isinstance(s, str) and s for s in servers)
    ):
        raise OAuthDiscoveryError(
            "protected-resource metadata is missing resource/authorization_servers"
        )
    return ProtectedResourceMetadata(
        resource=resource, authorization_servers=tuple(servers)
    )


def discover_authorization_server(
    client: httpx.Client,
    resource_metadata: ProtectedResourceMetadata,
    *,
    authorization_server_url: str | None = None,
) -> AuthorizationServerMetadata:
    """RFC 8414: fetch + validate the authorization-server metadata.

    Requires HTTPS, a matching issuer (issuer consistency — the returned ``issuer``
    field must equal the URL that was queried), the authorization_code grant, and
    PKCE S256 support. Any violation fails closed with :class:`OAuthDiscoveryError`.
    """
    issuer_url = authorization_server_url or resource_metadata.authorization_servers[0]
    if issuer_url not in resource_metadata.authorization_servers:
        raise OAuthDiscoveryError(
            "configured authorization server is not among the resource's declared servers"
        )
    metadata_url = _authorization_server_metadata_url(issuer_url)
    payload = _bounded_json_get(client, metadata_url)

    issuer = payload.get("issuer")
    authorization_endpoint = payload.get("authorization_endpoint")
    token_endpoint = payload.get("token_endpoint")
    challenge_methods = payload.get("code_challenge_methods_supported") or []
    grant_types = payload.get("grant_types_supported") or [
        "authorization_code",
        "implicit",
    ]
    registration_endpoint = payload.get("registration_endpoint")

    if not isinstance(issuer, str) or issuer.rstrip("/") != issuer_url.rstrip("/"):
        raise OAuthDiscoveryError("authorization-server issuer is inconsistent")
    if not isinstance(authorization_endpoint, str) or not isinstance(
        token_endpoint, str
    ):
        raise OAuthDiscoveryError("authorization-server metadata is incomplete")
    for endpoint in (authorization_endpoint, token_endpoint):
        parsed = urlsplit(endpoint)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise OAuthDiscoveryError("authorization-server endpoints must be HTTPS")
    if registration_endpoint is not None:
        if not isinstance(registration_endpoint, str):
            raise OAuthDiscoveryError(
                "authorization-server registration_endpoint is invalid"
            )
        parsed = urlsplit(registration_endpoint)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise OAuthDiscoveryError(
                "authorization-server registration_endpoint must be HTTPS"
            )
    if not isinstance(challenge_methods, list) or "S256" not in challenge_methods:
        raise OAuthDiscoveryError("authorization server does not support PKCE S256")
    if not isinstance(grant_types, list) or "authorization_code" not in grant_types:
        raise OAuthDiscoveryError(
            "authorization server does not support the authorization_code grant"
        )
    return AuthorizationServerMetadata(
        issuer=issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        code_challenge_methods_supported=tuple(challenge_methods),
        grant_types_supported=tuple(grant_types),
        registration_endpoint=registration_endpoint,
    )


class DynamicClientRegistrar(Protocol):
    """Pluggable RFC 7591 dynamic-client-registration hook.

    The unopinionated default remains pre-registered ``client_id``/``redirect_uri``
    on :class:`ProviderDescriptor` (``dynamic_client_registration=False``, the
    model default). :class:`Rfc7591DynamicClientRegistrar` is the concrete
    implementation a provider opts into with ``dynamic_client_registration=True``;
    a deployment that needs different registration semantics (e.g. an
    out-of-band-approved client) supplies its own implementation of this
    protocol instead. The broker never invents or silently switches a client
    id/issuer on its own — see :class:`RemoteOAuthBroker`'s ``dcr_registrar``
    constructor argument.
    """

    def register(
        self, provider: ProviderDescriptor, as_metadata: AuthorizationServerMetadata
    ) -> str:
        """Return the registered ``client_id`` for ``provider`` at this issuer."""
        ...


#: RFC 7591 ``token_endpoint_auth_method`` for a public PKCE client.
_PUBLIC_CLIENT_AUTH_METHOD = "none"
_MAX_DCR_RESPONSE_BYTES = 64 * 1024
_MAX_CLIENT_NAME_LEN = 128


def _bounded_registration_post(
    client: httpx.Client, url: str, body: dict[str, Any]
) -> dict[str, Any]:
    """RFC 7591 §3.1 registration request: bounded JSON POST, HTTPS-only."""
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise OAuthDiscoveryError("registration endpoint must be an exact HTTPS URL")
    try:
        with client.stream(
            "POST",
            url,
            json=body,
            headers={"Accept": "application/json"},
        ) as resp:
            resp.raise_for_status()
            response_body = bytearray()
            for chunk in resp.iter_bytes(65536):
                response_body.extend(chunk)
                if len(response_body) > _MAX_DCR_RESPONSE_BYTES:
                    raise OAuthDiscoveryError(
                        "registration response exceeded its bound"
                    )
    except httpx.HTTPError as exc:
        raise OAuthDiscoveryError("dynamic client registration request failed") from exc
    try:
        payload = json.loads(bytes(response_body))
    except (ValueError, UnicodeDecodeError):
        raise OAuthDiscoveryError(
            "dynamic client registration response was not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise OAuthDiscoveryError(
            "dynamic client registration response had an unexpected shape"
        )
    return payload


class Rfc7591DynamicClientRegistrar:
    """RFC 7591 public-client dynamic registration — the concrete DCR implementation.

    Registers exactly one public PKCE client per provider
    (``token_endpoint_auth_method="none"``, no client authentication secret
    requested or accepted — this broker only ever runs the public-client PKCE
    flow, U-43's model). Requires the authorization server to both advertise a
    ``registration_endpoint`` (RFC 8414 §2's optional DCR extension) and support
    PKCE ``S256`` (already enforced fail-closed by
    :func:`discover_authorization_server` before an :class:`AuthorizationServerMetadata`
    can even exist — re-checked here so this class is safe even if constructed
    and called directly, outside :class:`RemoteOAuthBroker`).

    Idempotency is NOT this class's job — it registers unconditionally every
    time :meth:`register` is called. :class:`RemoteOAuthBroker` (via
    :class:`_DynamicClientRegistrationCache`) is what makes registration
    idempotent-per-provider: it persists the winning ``client_id`` and never
    calls :meth:`register` again once a value is cached.
    """

    def __init__(self, http_client_factory: Any | None = None) -> None:
        self._http_client_factory = http_client_factory or _default_broker_http_client

    def register(
        self, provider: ProviderDescriptor, as_metadata: AuthorizationServerMetadata
    ) -> str:
        if "S256" not in as_metadata.code_challenge_methods_supported:
            raise OAuthDiscoveryError("authorization server does not support PKCE S256")
        if not as_metadata.registration_endpoint:
            raise OAuthProviderError(
                "authorization server does not advertise a dynamic client "
                "registration endpoint"
            )
        request_body = {
            "client_name": (
                f"agent-utilities-remote-oauth-broker:{provider.provider_id}"
            )[:_MAX_CLIENT_NAME_LEN],
            "redirect_uris": [provider.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": _PUBLIC_CLIENT_AUTH_METHOD,
            "application_type": "web",
        }
        client = self._http_client_factory()
        try:
            payload = _bounded_registration_post(
                client, as_metadata.registration_endpoint, request_body
            )
        finally:
            client.close()
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise OAuthProviderError(
                "dynamic client registration response is missing client_id"
            )
        # A returned ``client_secret`` (some servers issue one even for a
        # public-client request) is deliberately never read or stored: this
        # broker only ever authenticates as a public PKCE client, and storing
        # an unused secret would be one more thing that could leak.
        return client_id


class _DynamicClientRegistrationCache:
    """Idempotent-per-provider persisted ``client_id`` cache for RFC 7591 DCR.

    "Register once, persist the registration, never re-register on every flow"
    (the DCR deliverable's own idempotency requirement): a cached ``client_id``
    is reused for every subsequent :meth:`RemoteOAuthBroker.begin`. Concurrent
    first-callers in ONE process serialize on a per-provider lock
    (:func:`_lock_for`, the same primitive token refresh uses); across
    processes, :meth:`~agent_utilities.security.secrets_client.SecretsClient.set_if_absent`
    is the durable engine backend's atomic create-if-absent, so exactly one
    racer's registration wins and every other racer reads back the winner's
    ``client_id`` rather than persisting its own (wasted, but harmless) extra
    registration.
    """

    def __init__(self, secrets_client: Any) -> None:
        self._secrets = secrets_client

    @staticmethod
    def _key(provider_id: str) -> str:
        return f"oauth-dcr-client:{provider_id}"

    def get(self, provider_id: str) -> str | None:
        return self._secrets.get(self._key(provider_id))

    def get_or_register(
        self,
        provider: ProviderDescriptor,
        as_metadata: AuthorizationServerMetadata,
        registrar: DynamicClientRegistrar,
    ) -> str:
        key = self._key(provider.provider_id)
        cached = self._secrets.get(key)
        if cached:
            return cached
        with _lock_for(key):
            cached = self._secrets.get(key)
            if cached:
                return cached
            client_id = registrar.register(provider, as_metadata)
            if not isinstance(client_id, str) or not client_id:
                raise OAuthProviderError(
                    "dynamic client registration returned an invalid client_id"
                )
            if self._secrets.set_if_absent(key, client_id):
                return client_id
            # A concurrent registration in another process won the race; use
            # ITS client_id (this process's own registration, if the AS
            # actually created a distinct client for it, is simply unused).
            winner = self._secrets.get(key)
            return winner or client_id


# ---------------------------------------------------------------------------
# 4. Authorization transaction — encrypted, session-bound, short TTL.
# ---------------------------------------------------------------------------
def _generate_pkce() -> tuple[str, str]:
    """PKCE S256 verifier/challenge. Deliberately not imported from
    ``security.browser_auth`` — see the module docstring's U-43 rationale."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    return verifier, challenge


@dataclass(frozen=True)
class OAuthTransaction:
    state: str
    nonce: str
    verifier: str
    provider_id: str
    tenant_id: str
    principal_id: str
    browser_session_id: str
    redirect_uri: str
    scope: str
    created_at: float
    expires_at: float
    client_id: str = ""
    """The client_id actually used to build the authorization URL — resolved once
    at begin() time (pre-registered or DCR-cached) and pinned into the
    transaction so callback()'s token exchange uses the SAME client_id even if
    a concurrent DCR registration elsewhere changed the cache in between."""

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def to_json(self) -> str:
        return json.dumps(
            {
                "state": self.state,
                "nonce": self.nonce,
                "verifier": self.verifier,
                "provider_id": self.provider_id,
                "tenant_id": self.tenant_id,
                "principal_id": self.principal_id,
                "browser_session_id": self.browser_session_id,
                "redirect_uri": self.redirect_uri,
                "scope": self.scope,
                "created_at": self.created_at,
                "expires_at": self.expires_at,
                "client_id": self.client_id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> OAuthTransaction:
        data = json.loads(raw)
        return cls(**data)


class TransactionStore:
    """Session-bound encrypted transaction storage, single-use by atomic claim.

    Backed by the same :class:`~agent_utilities.security.secrets_client.SecretsClient`
    every other secret in this repo uses (engine-backed encrypted store, or
    OpenBao/Vault when ``SECRETS_BACKEND=vault`` — never a new store). Single-use is
    enforced with :meth:`SecretsClient.compare_and_set` (already atomic on the durable
    engine backend, the same primitive signing-key rotation uses) rather than a
    read-then-delete race: exactly one caller's compare-and-set from the exact bytes it
    read to a tombstone can win, so a concurrent replay of the same ``state`` fails
    closed even under real concurrency, not just under one process's lock.
    """

    def __init__(self, secrets_client: Any) -> None:
        self._secrets = secrets_client

    @staticmethod
    def _key(state: str) -> str:
        return f"oauth-tx:{state}"

    def put(self, tx: OAuthTransaction) -> None:
        self._secrets.set(self._key(tx.state), tx.to_json(), kind="oauth-transaction")

    def consume_once(self, state: str, *, now: float | None = None) -> OAuthTransaction:
        if (
            not isinstance(state, str)
            or not state
            or len(state) > _MAX_STATE_LEN
            or any(ord(character) < 32 or ord(character) == 127 for character in state)
        ):
            raise OAuthStateError("malformed state")
        key = self._key(state)
        raw = self._secrets.get(key)
        if raw is None or raw == _CONSUMED_TOMBSTONE:
            raise OAuthStateError("unknown, expired, or already-consumed state")
        claimed = self._secrets.compare_and_set(key, raw, _CONSUMED_TOMBSTONE)
        if not claimed:
            raise OAuthStateError("state was concurrently consumed (replay)")
        self._secrets.delete(key)
        try:
            tx = OAuthTransaction.from_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise OAuthStateError("stored transaction was malformed") from None
        if tx.state != state or tx.is_expired(now if now is not None else time.time()):
            raise OAuthStateError("authorization transaction expired or mismatched")
        return tx


# ---------------------------------------------------------------------------
# 6/7. Token store — encrypted, versioned-key, per-principal; refresh/revoke.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StoredToken:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_at: float
    granted_scope: str
    key_version: int
    audience: str
    # Process-owned identity for the exact grant.  It is deliberately separate
    # from bearer/refresh material and is rotated on callback/refresh.
    grant_revision: str = ""


@dataclass(frozen=True)
class OAuthGrantBinding:
    """Non-secret identity of one verified, current broker grant.

    The fingerprint schema is shared with EG fleet discovery records. A
    registry read can disclose an OAuth-scoped row only while this exact grant
    revision remains in the process-owned token store.
    """

    tenant_id: str
    principal_id: str
    provider_id: str
    resource_url: str
    audience: str
    granted_scopes: tuple[str, ...]
    key_version: int
    grant_revision: str

    @property
    def fingerprint(self) -> str:
        material = {
            "schema": "au.oauth-grant-binding.v1",
            "tenant": self.tenant_id,
            "principal": self.principal_id,
            "provider": self.provider_id,
            "resource": self.resource_url,
            "audience": self.audience,
            "scopes": list(self.granted_scopes),
            "key_version": self.key_version,
            "grant_revision": self.grant_revision,
        }
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _normalize_granted_scopes(value: str) -> tuple[str, ...]:
    """Return the canonical, non-secret representation of granted scopes."""

    return tuple(sorted({part for part in str(value or "").split() if part}))


_TOKEN_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_TOKEN_LOCKS_GUARD = threading.Lock()


def _lock_for(storage_key: str) -> threading.Lock:
    """Per-token lock, never a global lock — concurrent refreshes for different
    tokens must not serialize against each other (lane resource budget)."""
    with _TOKEN_LOCKS_GUARD:
        lock = _TOKEN_LOCKS.get(storage_key)
        if lock is None:
            lock = threading.Lock()
            _TOKEN_LOCKS[storage_key] = lock
        return lock


def _new_grant_revision() -> str:
    """Mint a process-owned opaque identity for one grant revision."""

    return secrets.token_hex(24)


class OAuthTokenStore:
    """Encrypted token custody keyed by tenant/principal/provider/resource/audience.

    The storage key is DERIVED ONLY from a verified, authenticated
    :class:`ActorContext` — never from a caller-supplied tenant/principal string.
    This is the structural mechanism, not a policy statement, behind "one user must
    never be able to reach another's token": there is no code path in this class that
    accepts an arbitrary principal identifier, so a caller can only ever address ITS
    OWN storage key.
    """

    CURRENT_KEY_VERSION = 1

    def __init__(self, secrets_client: Any) -> None:
        self._secrets = secrets_client

    @staticmethod
    def _require_verified(actor: ActorContext) -> tuple[str, str]:
        tenant = str(actor.tenant_id or "").strip()
        principal = str(actor.actor_id or "").strip()
        if not actor.authenticated or not tenant or not principal:
            raise PermissionError(
                "OAuth token custody requires a verified, authenticated principal "
                "with a tenant claim"
            )
        return tenant, principal

    @staticmethod
    def _storage_key(
        tenant: str, principal: str, provider_id: str, resource_url: str, audience: str
    ) -> str:
        # Key NAMES stay queryable plaintext metadata in the secrets backend (see
        # SecretsClient's own docstring); hash the principal-identifying components so
        # the key namespace itself never carries a readable actor id / tenant name.
        digest = hashlib.sha256(
            "\n".join((tenant, principal, provider_id, resource_url, audience)).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"oauth-token:{digest}"

    def put(
        self,
        *,
        actor: ActorContext,
        provider_id: str,
        resource_url: str,
        audience: str,
        token: StoredToken,
        authorization_started_at: float | None = None,
    ) -> None:
        tenant, principal = self._require_verified(actor)
        if token.audience != audience:
            raise OAuthBindingError(
                "stored token audience must equal the protected resource audience"
            )
        grant_revision = (
            str(token.grant_revision or "").strip() or _new_grant_revision()
        )
        key = self._storage_key(tenant, principal, provider_id, resource_url, audience)
        record = {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": token.token_type,
            "expires_at": token.expires_at,
            "granted_scope": token.granted_scope,
            "key_version": token.key_version,
            "audience": token.audience,
            "grant_revision": grant_revision,
            "revoked": False,
        }
        lock = _lock_for(key)
        with lock:
            raw = self._secrets.get(key)
            if raw is not None:
                try:
                    current = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    current = None
                if isinstance(current, dict) and current.get("revoked"):
                    try:
                        revoked_at = float(current["revoked_at"])
                        if authorization_started_at is None:
                            raise TypeError("authorization start time is required")
                        started_at = float(authorization_started_at)
                    except (KeyError, TypeError, ValueError, OverflowError):
                        raise OAuthRevokedError(
                            "revoked token requires a newer authorization flow"
                        ) from None
                    if (
                        not math.isfinite(revoked_at)
                        or not math.isfinite(started_at)
                        or started_at <= revoked_at
                    ):
                        raise OAuthRevokedError(
                            "authorization flow predates the token revocation"
                        )
            self._secrets.set(key, json.dumps(record, separators=(",", ":")))

    def get(
        self,
        *,
        actor: ActorContext,
        provider_id: str,
        resource_url: str,
        audience: str,
    ) -> StoredToken | None:
        """Return the caller's OWN token, or ``None``. Never another principal's."""
        tenant, principal = self._require_verified(actor)
        key = self._storage_key(tenant, principal, provider_id, resource_url, audience)
        raw = self._secrets.get(key)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("revoked"):
            # Fail closed: a revoked or malformed record reads as absent, never as a
            # usable token.
            return None
        try:
            stored_audience = str(data["audience"])
            grant_revision = str(data["grant_revision"]).strip()
            if stored_audience != audience or not grant_revision:
                return None
            return StoredToken(
                access_token=str(data["access_token"]),
                refresh_token=data.get("refresh_token"),
                token_type=str(data.get("token_type", "Bearer")),
                expires_at=float(data["expires_at"]),
                granted_scope=str(data.get("granted_scope", "")),
                key_version=int(data.get("key_version", self.CURRENT_KEY_VERSION)),
                audience=stored_audience,
                grant_revision=grant_revision,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def refresh(
        self,
        *,
        actor: ActorContext,
        provider: ProviderDescriptor,
        resource_url: str,
        audience: str,
        http_client: httpx.Client,
        client_id: str | None = None,
    ) -> StoredToken:
        """Rotate the refresh token atomically under a per-token lock.

        Fails closed: an absent record, a revoked record, or a missing refresh
        token all raise rather than degrading to "nothing to do" — the caller must
        treat any of these as "re-authenticate", never as "proceed unauthenticated".

        ``client_id`` is the client identity to present in the refresh grant.
        Defaults to ``provider.client_id`` (the pre-registered case); a
        DCR-enabled provider has no ``provider.client_id`` and the caller must
        pass the DCR-cached ``client_id`` explicitly (see
        :class:`RemoteOAuthBroker`, which resolves it via
        ``_DynamicClientRegistrationCache`` before calling this method).
        """
        tenant, principal = self._require_verified(actor)
        key = self._storage_key(
            tenant, principal, provider.provider_id, resource_url, audience
        )
        effective_client_id = client_id or provider.client_id
        if not effective_client_id:
            raise OAuthProviderError(
                "no client_id available for token refresh — DCR has not "
                "completed for this provider"
            )
        lock = _lock_for(key)
        with lock:
            raw = self._secrets.get(key)
            if raw is None:
                raise OAuthTokenAbsentError(
                    "no stored token for this principal/provider"
                )
            try:
                current = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise OAuthRevokedError(
                    "stored record is unreadable; ambiguous -> revoked"
                ) from None
            if not isinstance(current, dict) or current.get("revoked"):
                raise OAuthRevokedError("token is revoked")
            refresh_token = current.get("refresh_token")
            if not refresh_token:
                raise OAuthTokenAbsentError("no refresh token on this record")
            # The refresh grant goes to the authorization server's token
            # endpoint, never the protected resource. Re-discover here (not
            # cached) so a rotated token endpoint is always honored, mirroring
            # begin()/callback().
            resource_meta = discover_protected_resource(
                http_client, provider.resource_url
            )
            as_meta = discover_authorization_server(
                http_client,
                resource_meta,
                authorization_server_url=provider.authorization_server_url,
            )
            payload = _exchange_refresh_token(
                http_client, as_meta.token_endpoint, effective_client_id, refresh_token
            )
            new_record = {
                "access_token": payload["access_token"],
                "refresh_token": payload.get("refresh_token", refresh_token),
                "token_type": payload.get("token_type", "Bearer"),
                "expires_at": time.time() + float(payload.get("expires_in", 3600)),
                "granted_scope": payload.get("scope", current.get("granted_scope", "")),
                "key_version": self.CURRENT_KEY_VERSION,
                "audience": audience,
                "grant_revision": _new_grant_revision(),
                "revoked": False,
            }
            rotated = self._secrets.compare_and_set(
                key, raw, json.dumps(new_record, separators=(",", ":"))
            )
            if not rotated:
                raise OAuthRefreshRaceError(
                    "token record changed during refresh; retry"
                )
            return StoredToken(
                access_token=new_record["access_token"],
                refresh_token=new_record["refresh_token"],
                token_type=new_record["token_type"],
                expires_at=new_record["expires_at"],
                granted_scope=new_record["granted_scope"],
                key_version=new_record["key_version"],
                audience=new_record["audience"],
                grant_revision=new_record["grant_revision"],
            )

    def revoke(
        self,
        *,
        actor: ActorContext,
        provider_id: str,
        resource_url: str,
        audience: str,
    ) -> None:
        """Fail-closed revoke: always writes a tombstone record.

        An ambiguous outcome (record already gone, backend write partially failed,
        concurrent mutation) must never be readable as "still valid" — so this writes
        an explicit ``revoked: true`` record rather than merely attempting a delete.
        :meth:`get` treats ``revoked`` the same as absent.
        """
        tenant, principal = self._require_verified(actor)
        key = self._storage_key(tenant, principal, provider_id, resource_url, audience)
        lock = _lock_for(key)
        with lock:
            self._secrets.set(
                key,
                json.dumps(
                    {"revoked": True, "revoked_at": time.time()},
                    separators=(",", ":"),
                ),
            )


# ---------------------------------------------------------------------------
# Token exchange (mock-server tested only in this lane; see module docstring)
# ---------------------------------------------------------------------------
def _bounded_token_post(
    client: httpx.Client, url: str, data: dict[str, str]
) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise OAuthDiscoveryError("token endpoint must be an exact HTTPS URL")
    try:
        with client.stream("POST", url, data=data) as resp:
            resp.raise_for_status()
            body = bytearray()
            for chunk in resp.iter_bytes(65536):
                body.extend(chunk)
                if len(body) > _MAX_TOKEN_RESPONSE_BYTES:
                    raise OAuthDiscoveryError("token response exceeded its bound")
    except httpx.HTTPError as exc:
        raise OAuthDiscoveryError("token exchange request failed") from exc
    try:
        payload = json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError):
        raise OAuthDiscoveryError("token response was not valid JSON") from None
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise OAuthDiscoveryError("token response is missing access_token")
    return payload


def _exchange_authorization_code(
    client: httpx.Client,
    as_metadata: AuthorizationServerMetadata,
    *,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return _bounded_token_post(client, as_metadata.token_endpoint, data)


def _exchange_refresh_token(
    client: httpx.Client, token_endpoint: str, client_id: str, refresh_token: str
) -> dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    return _bounded_token_post(client, token_endpoint, data)


# ---------------------------------------------------------------------------
# Sanitized audit — never a code/token/state/verifier value.
# ---------------------------------------------------------------------------
def _pseudonymize(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _audit(
    event: str,
    *,
    provider: str,
    tenant: str,
    principal: str,
    outcome: str,
    latency_ms: float | None = None,
) -> None:
    _audit_logger.info(
        "oauth_broker event=%s provider=%s tenant=%s principal=%s outcome=%s latency_ms=%s",
        event,
        provider,
        _pseudonymize(tenant),
        _pseudonymize(principal),
        outcome,
        latency_ms,
    )


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------
def _default_broker_http_client() -> httpx.Client:
    from agent_utilities.core.http_client import create_http_client

    return create_http_client(
        timeout=_DISCOVERY_TIMEOUT_S,
        verify=True,
        follow_redirects=False,
        trust_env=False,
    )


class RemoteOAuthBroker:
    """Provider-agnostic remote browser-OAuth broker.

    Construction takes a :class:`ProviderRegistry` (administrator-populated) and an
    optional :class:`~agent_utilities.security.secrets_client.SecretsClient` (defaults
    to :func:`agent_utilities.security.secrets_client.create_secrets_client` — the
    same OpenBao/engine-backed encrypted store every other secret in this repo uses).
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        secrets_client: Any | None = None,
        http_client_factory: Any | None = None,
        dcr_registrar: DynamicClientRegistrar | None = None,
    ) -> None:
        if secrets_client is None:
            from agent_utilities.security.secrets_client import create_secrets_client

            secrets_client = create_secrets_client()
        self.registry = registry
        self.transactions = TransactionStore(secrets_client)
        self.tokens = OAuthTokenStore(secrets_client)
        self._http_client_factory = http_client_factory or _default_broker_http_client
        # Deliverable 1 (RFC 7591): the concrete registrar a
        # ``dynamic_client_registration=True`` provider uses, plus the
        # idempotent-per-provider cache of what it returned. Never constructed
        # eagerly against a real network — only invoked from begin() when a
        # provider actually opts in and no cached client_id exists yet.
        self._dcr_registrar: DynamicClientRegistrar = (
            dcr_registrar or Rfc7591DynamicClientRegistrar(self._http_client_factory)
        )
        self._dcr_cache = _DynamicClientRegistrationCache(secrets_client)

    def _resolve_client_id(
        self, provider: ProviderDescriptor, as_meta: AuthorizationServerMetadata
    ) -> str:
        """Effective client_id for one flow: pre-registered, or DCR (Deliverable 1).

        A DCR-enabled provider registers AT MOST ONCE, ever — subsequent calls
        (this method, called again on the next begin()) read the persisted
        cache instead of re-registering, which is what "idempotent per
        provider" means here.
        """
        if provider.client_id:
            return provider.client_id
        return self._dcr_cache.get_or_register(provider, as_meta, self._dcr_registrar)

    def begin(
        self,
        *,
        provider_id: str,
        actor: ActorContext,
        browser_session_id: str,
        requested_scopes: tuple[str, ...] | None = None,
    ) -> str:
        """Stage 1-4: discover, mint a transaction, return the authorization URL.

        This is the only method meant to be exercised against a REAL provider in
        this lane (the explicit "stop at the consent URL" gate); it is still only
        ever tested here against a mock authorization server.

        ``requested_scopes``, when given, must be a subset of the provider's
        administrator-configured ``scopes`` (a policy ceiling this broker never
        exceeds) — the gateway's ``POST /providers/{id}/authorize`` route uses
        this to apply a caller/policy-specific scope filter without the broker
        ever trusting a caller-supplied scope it wasn't already willing to grant.
        """
        if not isinstance(browser_session_id, str) or not browser_session_id:
            raise OAuthBindingError("a browser session id is required")
        tenant, principal = OAuthTokenStore._require_verified(actor)
        provider = self.registry.require_enabled(provider_id)
        if requested_scopes is not None:
            if not set(requested_scopes) <= set(provider.scopes):
                raise OAuthScopeError(
                    "requested scope exceeds the provider's configured scopes"
                )
            effective_scopes: tuple[str, ...] = tuple(requested_scopes)
        else:
            effective_scopes = provider.scopes
        client = self._http_client_factory()
        try:
            resource_meta = discover_protected_resource(client, provider.resource_url)
            as_meta = discover_authorization_server(
                client,
                resource_meta,
                authorization_server_url=provider.authorization_server_url,
            )
            client_id = self._resolve_client_id(provider, as_meta)
        finally:
            client.close()

        verifier, challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(16)
        now = time.time()
        scope = " ".join(effective_scopes)
        tx = OAuthTransaction(
            state=state,
            nonce=nonce,
            verifier=verifier,
            provider_id=provider.provider_id,
            tenant_id=tenant,
            principal_id=principal,
            browser_session_id=browser_session_id,
            redirect_uri=provider.redirect_uri,
            scope=scope,
            created_at=now,
            expires_at=now + _TRANSACTION_TTL_S,
            client_id=client_id,
        )
        self.transactions.put(tx)
        _audit(
            "begin",
            provider=provider.provider_id,
            tenant=tenant,
            principal=principal,
            outcome="authorization_url_issued",
        )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": provider.redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{as_meta.authorization_endpoint}?{urlencode(params)}"

    def callback(
        self,
        *,
        code: str,
        state: str,
        actor: ActorContext,
        browser_session_id: str,
    ) -> StoredToken:
        """Stage 5-6: validate the callback, exchange the code, store the token.

        Mock-server tested only in this lane (see module docstring). Rejects
        missing/replayed state, principal/tenant/session mismatch, and scope
        widening, all before any network call is made against the token endpoint
        except the discovery + exchange calls themselves.
        """
        tx = self.transactions.consume_once(state)
        tenant, principal = OAuthTokenStore._require_verified(actor)
        if principal != tx.principal_id or tenant != tx.tenant_id:
            _audit(
                "callback",
                provider=tx.provider_id,
                tenant=tenant,
                principal=principal,
                outcome="rejected_principal_mismatch",
            )
            raise OAuthBindingError(
                "callback principal/tenant does not match the initiating transaction"
            )
        if browser_session_id != tx.browser_session_id:
            _audit(
                "callback",
                provider=tx.provider_id,
                tenant=tenant,
                principal=principal,
                outcome="rejected_session_mismatch",
            )
            raise OAuthBindingError(
                "callback session does not match the initiating browser session"
            )
        provider = self.registry.require_enabled(tx.provider_id)
        client = self._http_client_factory()
        try:
            resource_meta = discover_protected_resource(client, provider.resource_url)
            as_meta = discover_authorization_server(
                client,
                resource_meta,
                authorization_server_url=provider.authorization_server_url,
            )
            payload = _exchange_authorization_code(
                client,
                as_meta,
                client_id=tx.client_id or provider.client_id or "",
                code=code,
                verifier=tx.verifier,
                redirect_uri=tx.redirect_uri,
            )
        finally:
            client.close()

        granted_scope = str(payload.get("scope", tx.scope))
        requested = set(tx.scope.split())
        granted = set(granted_scope.split())
        if not granted <= requested:
            _audit(
                "callback",
                provider=provider.provider_id,
                tenant=tenant,
                principal=principal,
                outcome="rejected_scope_widened",
            )
            raise OAuthScopeError("granted scope exceeds what was requested")

        access_token = payload["access_token"]
        if not isinstance(access_token, str) or not access_token:
            raise OAuthDiscoveryError(
                "token response contained an invalid access token"
            )
        token = StoredToken(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            token_type=str(payload.get("token_type", "Bearer")),
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
            granted_scope=granted_scope,
            key_version=OAuthTokenStore.CURRENT_KEY_VERSION,
            audience=provider.resource_url,
            grant_revision=_new_grant_revision(),
        )
        self.tokens.put(
            actor=actor,
            provider_id=provider.provider_id,
            resource_url=provider.resource_url,
            audience=provider.resource_url,
            token=token,
            authorization_started_at=tx.created_at,
        )
        _audit(
            "callback",
            provider=provider.provider_id,
            tenant=tenant,
            principal=principal,
            outcome="token_stored",
        )
        return token

    def revoke(self, *, actor: ActorContext, provider_id: str) -> None:
        """Revoke the verified caller's grant for an enabled provider.

        Provider identity and resource/audience binding come from the
        administrator-owned registry, never from the request.  The token store
        writes its fail-closed tombstone even when no live token remains.
        """
        provider = self.registry.require_enabled(provider_id)
        tenant, principal = OAuthTokenStore._require_verified(actor)
        self.tokens.revoke(
            actor=actor,
            provider_id=provider.provider_id,
            resource_url=provider.resource_url,
            audience=provider.resource_url,
        )
        _audit(
            "revoke",
            provider=provider.provider_id,
            tenant=tenant,
            principal=principal,
            outcome="token_revoked",
        )

    def _resolved_token(
        self, *, actor: ActorContext, provider_id: str, resource_url: str
    ) -> StoredToken:
        """Resolve one live, provider-bound token for the verified actor."""

        provider = self.registry.require_enabled(provider_id)
        if resource_url != provider.resource_url:
            raise OAuthProviderError(
                "token forwarding is bound to the provider's exact registered resource"
            )
        OAuthTokenStore._require_verified(actor)
        token = self.tokens.get(
            actor=actor,
            provider_id=provider.provider_id,
            resource_url=provider.resource_url,
            audience=provider.resource_url,
        )
        if token is None:
            raise OAuthTokenAbsentError("no stored token for this principal/provider")
        if token.expires_at <= time.time():
            raise OAuthTokenAbsentError(
                "stored token is expired; re-authorization is required"
            )
        if token.audience != provider.resource_url:
            raise OAuthProviderError("stored token audience is not provider-bound")
        granted_scopes = _normalize_granted_scopes(token.granted_scope)
        if not set(granted_scopes) <= set(provider.scopes):
            raise OAuthScopeError("stored grant exceeds the provider scope ceiling")
        if token.key_version <= 0 or not token.grant_revision.strip():
            raise OAuthTokenAbsentError("stored grant identity is unavailable")
        return token

    def grant_binding_for(
        self, *, actor: ActorContext, provider_id: str, resource_url: str
    ) -> OAuthGrantBinding:
        """Read one current grant identity without exposing bearer material."""
        token = self._resolved_token(
            actor=actor, provider_id=provider_id, resource_url=resource_url
        )
        tenant, principal = OAuthTokenStore._require_verified(actor)
        return OAuthGrantBinding(
            tenant_id=tenant,
            principal_id=principal,
            provider_id=provider_id,
            resource_url=resource_url,
            audience=token.audience,
            granted_scopes=_normalize_granted_scopes(token.granted_scope),
            key_version=token.key_version,
            grant_revision=token.grant_revision,
        )

    def bearer_headers_for(
        self, *, actor: ActorContext, provider_id: str, resource_url: str
    ) -> dict[str, str]:
        """Endpoint-bound bearer lookup: forwards ONLY to the exact registered resource.

        This is the seam a per-principal multiplexer child calls per outbound
        request, via :func:`graph_os.fleet.multiplexer._resolve_remote_oauth_bearer`.
        ``resource_url`` must equal the provider's registered exact resource URL,
        or this raises rather than forwarding a token to a different endpoint
        (rejects cross-provider forwarding, and — because the caller passes the
        URL it is ACTUALLY about to connect to, never one echoed back from a
        redirect — rejects forwarding to a rebound/redirected endpoint too).

        Fails closed on an EXPIRED token exactly like a missing one: this method
        never transparently refreshes (the per-call forwarding hot path stays
        synchronous and side-effect-free; refresh is a separate, deliberately
        out-of-band operation via :meth:`OAuthTokenStore.refresh`). An expired
        grant reads as "no valid grant" — the caller must re-run the browser
        flow, not silently receive a stale credential.
        """
        token = self._resolved_token(
            actor=actor, provider_id=provider_id, resource_url=resource_url
        )
        return {"Authorization": f"{token.token_type} {token.access_token}"}
