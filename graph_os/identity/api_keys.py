"""Typed model for expiring API keys / service accounts (GRAPHOS-IDENTITY-R008).

Slice .1: the typed model, construction validation, and refusal tests only.
Scope-intersection-at-use-time and revocation wiring are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import IdentityUnavailable

_APPROVER_SCOPES = frozenset({"approver", "approvals:decide"})


@dataclass(frozen=True, slots=True)
class ApiKeyGrant:
    """An API key's declared scopes, refused if any scope is approver-class."""

    key_id: str
    owner_principal_id: str
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id:
            raise IdentityUnavailable("API key requires a key id")
        if not isinstance(self.owner_principal_id, str) or not self.owner_principal_id:
            raise IdentityUnavailable("API key requires an owner principal id")
        rejected = self.scopes & _APPROVER_SCOPES
        if rejected:
            raise IdentityUnavailable(
                f"API keys may not carry approver-class scope(s): {sorted(rejected)}"
            )
