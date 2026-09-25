"""The persistent local token issuer (IDM-06).

GraphOS signs every access token the platform's one authorization path
consumes, in every auth mode: an authenticator resolves a principal, this
issuer mints a short-lived RS256 token whose ``sub`` IS the principal id, and
agent-utilities and the engine verify it through the published JWKS.

The signing keys live in the configured secrets backend (engine storage or
OpenBao through agent-utilities' ``SecretsBackend``), never in config or on
disk beside the process, so every GraphOS replica signs with the same ring and
the ring survives restarts. Rotation writes the whole ring with a
compare-and-set: two replicas rotating at once converge on one winner. A
retired key stays published for one access-token lifetime (so tokens minted a
moment before the rotation still verify) unless the rotation is a revocation,
which drops it at once (leaving ``none`` mode: tokens minted in demo mode must
die immediately).
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from .engine import Resolution

__all__ = [
    "ACCESS_TOKEN_MAX_SECONDS",
    "ISSUER_KEYS_SECRET",
    "IssuerSettings",
    "KeyRing",
    "LocalIssuer",
    "Retirement",
    "SecretStore",
    "TokenGrant",
]

#: Secrets-backend key holding the whole signing-key ring (one JSON document).
ISSUER_KEYS_SECRET = "graph-os/identity/issuer-signing-keys"
#: Design §3.3: access tokens live at most five minutes.
ACCESS_TOKEN_MAX_SECONDS = 300
_KEY_BITS = 2048
_RING_WRITE_ATTEMPTS = 4


class SecretStore(Protocol):
    """The slice of agent-utilities' ``SecretsBackend`` the issuer needs."""

    def get(self, key: str) -> str | None: ...

    def set_if_absent(self, key: str, value: str, **metadata: Any) -> bool: ...

    def compare_and_set(
        self, key: str, expected: str, value: str, **metadata: Any
    ) -> bool: ...


class Retirement:
    """How a rotation treats the key it replaces."""

    #: Keep the old key published for one access-token lifetime.
    OVERLAP = "overlap"
    #: Stop publishing the old key now (tokens it signed die at once).
    REVOKE = "revoke"


@dataclass(frozen=True)
class IssuerSettings:
    """Where tokens are valid: issuer URL, engine audience and tenant."""

    issuer: str
    audience: str
    tenant: str
    access_ttl_seconds: int = ACCESS_TOKEN_MAX_SECONDS

    def __post_init__(self) -> None:
        if not (self.issuer and self.audience and self.tenant):
            raise ValueError("issuer, audience and tenant are all required")
        if not 1 <= self.access_ttl_seconds <= ACCESS_TOKEN_MAX_SECONDS:
            raise ValueError("access tokens live between 1 and 300 seconds")


@dataclass(frozen=True)
class TokenGrant:
    """How a token's principal authenticated (``amr``) and when."""

    methods: tuple[str, ...]
    auth_time: int
    #: Narrower scopes than the principal's (an API key); ``None`` = all.
    scopes: frozenset[str] | None = None


def _new_key() -> dict[str, Any]:
    key = RSAKey.generate_key(_KEY_BITS, private=True)
    kid = f"gos-{secrets.token_hex(8)}"
    return {**key.as_dict(private=True), "kid": kid, "use": "sig", "alg": "RS256"}


def _public(jwk: Mapping[str, Any]) -> dict[str, Any]:
    key = RSAKey.import_key(dict(jwk))
    return {**key.as_dict(private=False), "kid": jwk["kid"], "use": "sig", "alg": "RS256"}


@dataclass(frozen=True)
class KeyRing:
    """The active private key plus the retired public keys still published."""

    active: Mapping[str, Any]
    retired: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def fresh(cls) -> KeyRing:
        return cls(active=_new_key())

    @classmethod
    def parse(cls, text: str) -> KeyRing:
        document = json.loads(text)
        return cls(
            active=document["active"],
            retired=tuple(document.get("retired") or ()),
        )

    def dump(self) -> str:
        return json.dumps(
            {"active": dict(self.active), "retired": [dict(r) for r in self.retired]},
            sort_keys=True,
        )

    @property
    def kid(self) -> str:
        return str(self.active["kid"])

    def published(self, now: float) -> tuple[Mapping[str, Any], ...]:
        live = tuple(r for r in self.retired if float(r["retire_after"]) > now)
        return (_public(self.active), *(r["key"] for r in live))

    def rotated(self, now: float, retirement: str, overlap: int) -> KeyRing:
        """A ring under a new active key.

        ``OVERLAP`` keeps every still-live retired key plus the outgoing one;
        ``REVOKE`` publishes the new key alone, so no token signed before the
        rotation verifies any more.
        """
        if retirement == Retirement.REVOKE:
            return KeyRing(active=_new_key())
        kept = [r for r in self.retired if float(r["retire_after"]) > now]
        kept.append({"key": _public(self.active), "retire_after": now + overlap})
        return KeyRing(active=_new_key(), retired=tuple(kept))


class LocalIssuer:
    """Mint and publish RS256 access tokens over a persisted :class:`KeyRing`."""

    def __init__(
        self,
        store: SecretStore,
        settings: IssuerSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock

    @property
    def settings(self) -> IssuerSettings:
        return self._settings

    def ring(self) -> KeyRing:
        """The persisted ring, created on first use (race-safe)."""
        stored = self._store.get(ISSUER_KEYS_SECRET)
        if stored is None:
            self._store.set_if_absent(ISSUER_KEYS_SECRET, KeyRing.fresh().dump())
            stored = self._store.get(ISSUER_KEYS_SECRET)
        if stored is None:
            raise RuntimeError("the secrets backend did not keep the issuer key ring")
        return KeyRing.parse(stored)

    def rotate(self, retirement: str = Retirement.OVERLAP) -> str:
        """Replace the active key; answer the new ``kid``."""
        for _ in range(_RING_WRITE_ATTEMPTS):
            current = self._store.get(ISSUER_KEYS_SECRET)
            if current is None:
                self.ring()
                continue
            ring = KeyRing.parse(current).rotated(
                self._clock(), retirement, self._settings.access_ttl_seconds
            )
            if self._store.compare_and_set(ISSUER_KEYS_SECRET, current, ring.dump()):
                return ring.kid
        raise RuntimeError("issuer key rotation lost every compare-and-set race")

    def jwks(self) -> dict[str, Any]:
        return {"keys": [dict(key) for key in self.ring().published(self._clock())]}

    def discovery(self) -> dict[str, Any]:
        """The OpenID provider metadata GraphOS publishes for its issuer."""
        issuer = self._settings.issuer.rstrip("/")
        return {
            "issuer": self._settings.issuer,
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
            "token_endpoint": f"{issuer}/oauth/token",
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["token"],
            "subject_types_supported": ["public"],
            "grant_types_supported": [
                "urn:ietf:params:oauth:grant-type:token-exchange"
            ],
        }

    def claims_for(self, resolution: Resolution, grant: TokenGrant) -> dict[str, Any]:
        """The token body for ``resolution`` (``sub`` is the principal id)."""
        scopes = resolution.scopes
        if grant.scopes is not None:
            scopes = scopes & grant.scopes
        ordered = sorted(scopes)
        now = int(self._clock())
        return {
            "iss": self._settings.issuer,
            "sub": resolution.principal_id,
            "aud": self._settings.audience,
            "iat": now,
            "nbf": now - 1,
            "exp": now + self._settings.access_ttl_seconds,
            "jti": secrets.token_hex(16),
            "scope": " ".join(ordered),
            "roles": ordered,
            "realm_access": {"roles": ordered},
            "tenant_id": self._settings.tenant,
            "preferred_username": resolution.username,
            "principal_kind": resolution.kind,
            "amr": list(grant.methods),
            "auth_time": grant.auth_time,
        }

    def mint(self, resolution: Resolution, grant: TokenGrant) -> str:
        """A signed access token; refuses a principal that may not hold one."""
        if not resolution.usable:
            raise PermissionError("the principal may not hold a token")
        active = self.ring().active
        key = RSAKey.import_key(dict(active))
        header = {"alg": "RS256", "typ": "at+jwt", "kid": active["kid"]}
        return jwt.encode(header, self.claims_for(resolution, grant), key)

    def verify(self, token: str) -> dict[str, Any]:
        """Decode a token this issuer minted (signature, iss, aud, exp)."""
        key_set = KeySet.import_key_set(self.jwks())
        decoded = jwt.decode(token, key_set, algorithms=["RS256"])
        registry = jwt.JWTClaimsRegistry(
            leeway=5,
            iss={"essential": True, "value": self._settings.issuer},
            aud={"essential": True, "value": self._settings.audience},
            exp={"essential": True},
        )
        registry.validate(decoded.claims)
        return dict(decoded.claims)
