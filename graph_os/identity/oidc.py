"""Typed model for ordered OIDC mapping rules (GRAPHOS-IDENTITY-R009).

Slice .1: the typed model and construction validation.
Slice .2.1: pure ordered claim-to-rule evaluation (``select_mapping_rule``).
Slice .2.2: ID-token claim checks (``check_id_token_claims``).
Callback wiring is a later slice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .engine import IdentityUnavailable

_JIT_POLICIES = frozenset({"create", "deny"})


@dataclass(frozen=True, slots=True)
class OidcMappingRule:
    """One ordered mapping rule within a provider's deterministic rule list."""

    provider_id: str
    order: int
    claim_match: str
    jit_policy: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise IdentityUnavailable("OIDC mapping rule requires a provider id")
        if (
            not isinstance(self.order, int)
            or isinstance(self.order, bool)
            or self.order < 0
        ):
            raise IdentityUnavailable("OIDC mapping rule requires a non-negative order")
        if not isinstance(self.claim_match, str) or not self.claim_match:
            raise IdentityUnavailable("OIDC mapping rule requires a claim match")
        if self.jit_policy not in _JIT_POLICIES:
            raise IdentityUnavailable(
                f"unknown JIT policy {self.jit_policy!r}; expected one of {sorted(_JIT_POLICIES)}"
            )


def _claim_matches(claim_match: str, claims: Mapping[str, Any]) -> bool:
    """True when ``name=value`` matches a scalar claim or a member of a list claim."""
    name, sep, expected = claim_match.partition("=")
    if not sep or not name or not expected:
        raise IdentityUnavailable(
            f"malformed OIDC claim match {claim_match!r}; expected 'name=value'"
        )
    actual = claims.get(name)
    if isinstance(actual, str):
        return actual == expected
    if isinstance(actual, (list, tuple, set, frozenset)):
        return expected in actual
    return False


def select_mapping_rule(
    rules: Iterable[OidcMappingRule],
    provider_id: str,
    claims: Mapping[str, Any],
) -> OidcMappingRule:
    """Return the first rule (lowest ``order``) of the provider matching the claims.

    Fails closed: no matching rule, or two rules sharing an order, is refused.
    """
    candidates = sorted(
        (r for r in rules if r.provider_id == provider_id), key=lambda r: r.order
    )
    orders = [r.order for r in candidates]
    if len(set(orders)) != len(orders):
        raise IdentityUnavailable("OIDC mapping rules have ambiguous duplicate order")
    for rule in candidates:
        if _claim_matches(rule.claim_match, claims):
            return rule
    raise IdentityUnavailable("no OIDC mapping rule matches the presented claims")


class OidcRefusalReason(StrEnum):
    """Why decoded ID-token claims were refused."""

    WRONG_ISSUER = "wrong_issuer"
    WRONG_AUDIENCE = "wrong_audience"
    EXPIRED = "expired"
    MISSING_EXPIRY = "missing_expiry"
    NONCE_MISMATCH = "nonce_mismatch"


class OidcTokenRefused(IdentityUnavailable):
    """Decoded ID-token claims failed a check; carries the typed reason."""

    def __init__(self, reason: OidcRefusalReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def check_id_token_claims(
    claims: Mapping[str, Any],
    *,
    issuer: str,
    audience: str,
    nonce: str,
    now: datetime,
    clock_skew: timedelta = timedelta(0),
) -> None:
    """Refuse unless issuer, audience, expiry and nonce all match (no signature check)."""
    if now.tzinfo is None:
        raise IdentityUnavailable("OIDC check time must be timezone-aware")
    if not issuer or not audience or not nonce:
        raise IdentityUnavailable("OIDC check requires issuer, audience and nonce")
    if claims.get("iss") != issuer:
        raise OidcTokenRefused(
            OidcRefusalReason.WRONG_ISSUER, "ID token issuer does not match"
        )
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else aud
    if not isinstance(audiences, (list, tuple)) or audience not in audiences:
        raise OidcTokenRefused(
            OidcRefusalReason.WRONG_AUDIENCE, "ID token audience is not this client"
        )
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise OidcTokenRefused(
            OidcRefusalReason.MISSING_EXPIRY, "ID token has no usable expiry"
        )
    if now.astimezone(UTC) - clock_skew >= datetime.fromtimestamp(exp, UTC):
        raise OidcTokenRefused(OidcRefusalReason.EXPIRED, "ID token has expired")
    if claims.get("nonce") != nonce:
        raise OidcTokenRefused(
            OidcRefusalReason.NONCE_MISMATCH, "ID token nonce does not match"
        )
