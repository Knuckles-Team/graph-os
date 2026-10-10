"""Typed model for expiring API keys / service accounts (GRAPHOS-IDENTITY-R008).

Slice .1: the typed model, construction validation, and refusal tests only.
Scope-intersection-at-use-time and revocation wiring are later slices.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from .engine import IdentityUnavailable

_APPROVER_SCOPES = frozenset({"approver", "approvals:decide"})


@dataclass(frozen=True, slots=True)
class ApiKeyGrant:
    """An API key's declared scopes, refused if any scope is approver-class."""

    key_id: str
    owner_principal_id: str
    scopes: frozenset[str]
    expires_at: datetime | None = None

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


def effective_scopes(
    grant: ApiKeyGrant,
    owner_scopes: frozenset[str],
    *,
    now: datetime,
    revoked_key_ids: Collection[str] = (),
) -> frozenset[str]:
    """Scopes usable right now: key scopes intersected with the owner's current scopes.

    Fails closed: a revoked or expired key is refused immediately.
    """
    if grant.key_id in revoked_key_ids:
        raise IdentityUnavailable("API key has been revoked")
    if grant.expires_at is not None and now >= grant.expires_at:
        raise IdentityUnavailable("API key has expired")
    return (grant.scopes & owner_scopes) - _APPROVER_SCOPES
