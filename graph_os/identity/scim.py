"""Typed model for the SCIM 2.0 service credential (GRAPHOS-IDENTITY-R011).

Slice .1: the typed model, construction validation, and refusal tests only.
The create/update/patch/deactivate server surface is a later slice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import IdentityUnavailable


@dataclass(frozen=True, slots=True)
class ScimServiceCredential:
    """A SCIM service credential, scoped to exactly one provider."""

    provider_id: str
    token_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise IdentityUnavailable("SCIM credential requires a provider id")
        if not isinstance(self.token_id, str) or not self.token_id:
            raise IdentityUnavailable("SCIM credential requires a token id")

    def authorizes(self, requested_provider_id: str) -> bool:
        """A token scoped to a different provider is refused by its caller."""
        return requested_provider_id == self.provider_id
