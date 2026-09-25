"""Identity broker: auth modes, the local issuer, sessions, MFA, API keys and
the external authorities.

GraphOS is the gateway and composition authority (RF-ADR-009 §2); the
identity STORE is engine-owned (``Method::Identity`` in epistemic-graph).
This package owns every web and process flow around that store: the
persistent local token issuer and its JWKS, the ``none`` / ``local`` /
``external`` authenticators, server-side browser sessions with CSRF, second
factors, API keys for MCP and REST clients, and the ``graph-os-identity``
operator commands. It never holds a password hash, a session hash or a TOTP
secret: every secret is verified, hashed or sealed inside the engine.

The external authorities -- OIDC (:mod:`.oidc`, :mod:`.keycloak`), LDAP
(:mod:`.ldap`), SCIM (:mod:`.scim`), SAML (:mod:`.saml`) and the optional mail
adapter (:mod:`.smtp`) -- verify a protocol assertion and hand the engine one
``(idp_id, subject, claims)`` triple through :mod:`.idp_common`. They never hold
a principal record, a role or a credential of their own.

The one authorization path (IDENTITY-AND-AUTH-MODES-DESIGN §2.2): an
authenticator resolves ONE stable principal id, the local issuer mints a
short-lived RS256 access token for it, and that token enters the unchanged
agent-utilities claims → session → engine envelope path.
"""

from .engine import IdentityCall, IdentityEngine, IdentityRefused, IdentityUnavailable

__all__ = ["IdentityCall", "IdentityEngine", "IdentityRefused", "IdentityUnavailable"]
