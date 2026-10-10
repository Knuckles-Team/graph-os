"""Typed model for ordered OIDC mapping rules (GRAPHOS-IDENTITY-R009).

Slice .1: the typed model and construction validation.
Slice .2.1: pure ordered claim-to-rule evaluation (``select_mapping_rule``).
PKCE/state/nonce verification and the callback wiring are later slices.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
