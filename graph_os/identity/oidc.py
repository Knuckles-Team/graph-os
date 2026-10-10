"""Typed model for ordered OIDC mapping rules (GRAPHOS-IDENTITY-R009).

Slice .1: the typed model, construction validation, and refusal tests only.
PKCE/state/nonce verification and JIT policy enforcement are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

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
