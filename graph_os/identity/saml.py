"""Typed model for the native SAML service provider (GRAPHOS-IDENTITY-R012).

Slice .1: the typed model, construction validation, and refusal tests only.
Signature/audience/replay verification of an assertion is a later slice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import IdentityUnavailable


@dataclass(frozen=True, slots=True)
class SamlServiceProvider:
    """A configured SAML service-provider identity, bound to one IdP certificate."""

    entity_id: str
    acs_url: str
    idp_certificate_pem: str

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise IdentityUnavailable("SAML service provider requires an entity id")
        if not isinstance(self.acs_url, str) or not self.acs_url.startswith(
            ("https://", "http://")
        ):
            raise IdentityUnavailable(
                "SAML service provider requires a valid assertion consumer service URL"
            )
        if (
            not isinstance(self.idp_certificate_pem, str)
            or "BEGIN CERTIFICATE" not in self.idp_certificate_pem
        ):
            raise IdentityUnavailable(
                "SAML service provider requires a configured IdP certificate"
            )
