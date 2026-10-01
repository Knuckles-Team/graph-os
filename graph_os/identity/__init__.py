"""Identity core: the engine-backed identity port and its supporting types.

GraphOS is the gateway and composition authority; the identity STORE is
engine-owned (``Method::Identity`` in epistemic-graph). This package owns
every web and process flow around that store: the persistent local token
issuer and its JWKS (GRAPHOS-IDENTITY-R003), the ``none`` / ``local`` /
``external`` authenticators and loopback bootstrap guarding
(GRAPHOS-IDENTITY-R004), the shared RBAC decision oracle across identity
modes (GRAPHOS-IDENTITY-R014), and identity-ports-only wiring for the web
host, web co-service, and MCP server (GRAPHOS-IDENTITY-R021). It never
holds a password hash, a session hash or a TOTP secret: every secret is
verified, hashed or sealed inside the engine.

This slice exposes the core engine port -- :class:`.engine.IdentityEngine`,
:class:`.engine.IdentityCall`, and the typed refusal/unavailable errors --
and the caller-generated secret material helpers (:mod:`.material`). The
narrow served-identity port consumed by the optional WebUI host, the
authenticators, local issuer, sessions, MFA, API keys, and external
identity providers land in later changes.

The one authorization path: an authenticator resolves ONE stable principal
id, the local issuer mints a short-lived RS256 access token for it, and that
token enters the unchanged agent-utilities claims -> session -> engine
envelope path.
"""

from .engine import IdentityCall, IdentityEngine, IdentityRefused, IdentityUnavailable
from .material import (
    API_KEY_PREFIX,
    ApiKeySecret,
    client_ip_prefix,
    new_api_key,
    new_recovery_codes,
    new_token,
    new_totp_secret,
    parse_api_key,
    totp_provisioning_uri,
)

__all__ = [
    "API_KEY_PREFIX",
    "ApiKeySecret",
    "IdentityCall",
    "IdentityEngine",
    "IdentityRefused",
    "IdentityUnavailable",
    "client_ip_prefix",
    "new_api_key",
    "new_recovery_codes",
    "new_token",
    "new_totp_secret",
    "parse_api_key",
    "totp_provisioning_uri",
]
