"""Strict local claim preparation and signing with an existing injected key.

The serving issuer must obtain a credential-derived Resolution and its source
expiry from the qualified EG owner before calling this pure mapping. Neither
the DTO nor the mapping result is an authentication capability.
"""

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .engine import IdentityUnavailable, Resolution, _text
from .ports import CredentialAuthority, CredentialState, SigningKeyStore

ACCESS_TOKEN_MAX_SECONDS = 300
ISSUER_KEY_RING_PATH = "graph-os/identity/issuer-signing-keys"


@dataclass(frozen=True, slots=True)
class IssuerSettings:
    issuer: str
    audience: str
    tenant: str
    access_ttl_seconds: int = ACCESS_TOKEN_MAX_SECONDS

    def __post_init__(self) -> None:
        for value in (self.issuer, self.audience, self.tenant):
            _text(value)
        ttl = self.access_ttl_seconds
        if type(ttl) is not int or not 1 <= ttl <= ACCESS_TOKEN_MAX_SECONDS:
            raise ValueError("access tokens live between 1 and 300 seconds")


def claims_for(
    resolution: Resolution,
    settings: IssuerSettings,
    *,
    source_expires_at_ms: int,
    now_ms: int,
) -> dict[str, Any]:
    """Prepare exact local claims; missing/expired source authority refuses.

    Call Resolution.narrow first when the qualified credential supplies an
    additional scope ceiling. The returned token claims and that resolution's
    context then carry the same effective scope set.
    """
    if not isinstance(resolution, Resolution):
        raise IdentityUnavailable("a strict resolution is required")
    for value in (source_expires_at_ms, now_ms):
        if type(value) is not int or value < 0:
            raise IdentityUnavailable("authoritative source expiry and clock required")
    context = resolution.request_context
    if context["tenant"] != settings.tenant or context["audience"] != settings.audience:
        raise IdentityUnavailable("resolution deployment binding disagrees")
    issued = now_ms // 1000
    expires = min(issued + settings.access_ttl_seconds, source_expires_at_ms // 1000)
    if expires * 1000 <= now_ms:
        raise PermissionError("source credential cannot support a live token")
    return {
        "iss": settings.issuer,
        **identity_claims(resolution),
        "iat": issued,
        "nbf": issued,
        "exp": expires,
        "jti": secrets.token_hex(16),
    }


def identity_claims(resolution: Resolution) -> dict[str, Any]:
    """Exact profile mapping shared with the browser's verified-token check."""
    context = resolution.request_context
    return {
        "aud": context["audience"],
        "sub": resolution.principal_id,
        "tenant_id": context["tenant"],
        "principal_kind": resolution.kind,
        "roles": sorted(resolution.roles),
        "scope": " ".join(sorted(resolution.scopes)),
        "policy_version": context["policy_version"],
        "agent_id": context["agent_id"],
        "delegation": list(context["delegation"]),
    }


def require_token_binding(claims: Mapping[str, Any], resolution: Resolution) -> None:
    """Compare already-verified token facts, never verify a raw claim dict."""
    for name, expected in identity_claims(resolution).items():
        actual = claims.get(name)
        if name in {"roles", "delegation"}:
            if not isinstance(actual, (tuple, list)) or type(actual) not in (
                tuple,
                list,
            ):
                raise PermissionError("verified local token binding disagrees")
            actual = list(actual)
        if actual != expected:
            raise PermissionError("verified local token binding disagrees")


class LocalIssuer:
    """Sign only after injected credential authority; never provision or grant.

    The key store exposes the existing SecretsBackend.get contract. Missing
    ring/key or credential owner refuses. Key rotation remains the configured
    owner responsibility; this class never writes keys or creates a process key.
    """

    def __init__(
        self,
        store: SigningKeyStore,
        settings: IssuerSettings,
        authority: CredentialAuthority,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(getattr(store, "get", None)) or not callable(
            getattr(authority, "resolve_credential", None)
        ):
            raise IdentityUnavailable(
                "configured signing and credential owners required"
            )
        self._store = store
        self._settings = settings
        self._authority = authority
        self._clock = clock

    def _sign(self, claims: dict[str, Any]) -> str:
        from joserfc import jwt
        from joserfc.errors import JoseError
        from joserfc.jwk import RSAKey

        stored = self._store.get(ISSUER_KEY_RING_PATH)
        if type(stored) is not str or not stored:
            raise IdentityUnavailable("configured issuer signing key unavailable")
        try:
            active = json.loads(stored)["active"]
            if (
                not isinstance(active, dict)
                or active.get("kty") != "RSA"
                or active.get("alg") != "RS256"
                or active.get("use") != "sig"
                or type(active.get("d")) is not str
                or not active["d"]
            ):
                raise ValueError("invalid private signing key")
            kid = _text(active.get("kid"))
            key = RSAKey.import_key(active)
            return jwt.encode(
                {"alg": "RS256", "typ": "at+jwt", "kid": kid}, claims, key
            )
        except (KeyError, ValueError, TypeError, JoseError):
            raise IdentityUnavailable("configured issuer signing key invalid") from None

    async def issue(self, credential: str) -> str:
        if type(credential) is not str or not credential:
            raise PermissionError("an exact verified source credential is required")
        state = await self._authority.resolve_credential(credential)
        if not isinstance(state, CredentialState):
            raise IdentityUnavailable("qualified credential source expiry unavailable")
        claims = claims_for(
            state.resolution,
            self._settings,
            source_expires_at_ms=state.expires_at_ms,
            now_ms=int(self._clock() * 1000),
        )
        token = self._sign(claims)
        # Awaited owner work can revoke/narrow authority while signing is in flight.
        fresh = await self._authority.resolve_credential(credential)
        if not isinstance(fresh, CredentialState):
            raise IdentityUnavailable("qualified credential source expiry unavailable")
        if (
            fresh.resolution != state.resolution
            or claims["exp"] * 1000 > fresh.expires_at_ms
            or claims["exp"] * 1000 <= int(self._clock() * 1000)
        ):
            raise PermissionError("credential authority changed during issuance")
        return token
