"""Typed model for the native SAML service provider (GRAPHOS-IDENTITY-R012).

Slice .1: the typed model, construction validation, and refusal tests only.
Slice .2.1: the typed parsed-assertion model and the pure audience, recipient
and validity-window checks, each refused with a typed reason. Signature
verification, sign-in wiring and the live IdP probe are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

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


class SamlRefusalReason(StrEnum):
    """Why a parsed assertion was refused."""

    WRONG_AUDIENCE = "wrong_audience"
    WRONG_RECIPIENT = "wrong_recipient"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    SIGNATURE_VERIFIER_ABSENT = "signature_verifier_absent"
    INVALID_SIGNATURE = "invalid_signature"


class SamlAssertionRefused(IdentityUnavailable):
    """A parsed SAML assertion failed a validity check; carries the typed reason."""

    def __init__(self, reason: SamlRefusalReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ParsedSamlAssertion:
    """The already-parsed claims of one assertion (no XML handled here)."""

    assertion_id: str
    issuer: str
    subject: str
    audiences: tuple[str, ...]
    recipient: str
    not_before: datetime
    not_on_or_after: datetime

    def __post_init__(self) -> None:
        for name in ("assertion_id", "issuer", "subject", "recipient"):
            _require_text(getattr(self, name), f"SAML assertion requires a {name}")
        if not self.audiences:
            raise IdentityUnavailable("SAML assertion requires an audience")
        for audience in self.audiences:
            _require_text(audience, "SAML assertion requires an audience")
        for name in ("not_before", "not_on_or_after"):
            _require_aware(getattr(self, name), name)
        if self.not_on_or_after <= self.not_before:
            raise IdentityUnavailable("SAML assertion validity window is empty")


def _require_text(value: object, message: str) -> None:
    if not isinstance(value, str) or not value:
        raise IdentityUnavailable(message)


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IdentityUnavailable(
            f"SAML assertion {name} must be a timezone-aware datetime"
        )


def check_assertion_conditions(
    provider: SamlServiceProvider,
    assertion: ParsedSamlAssertion,
    now: datetime,
    clock_skew: timedelta = timedelta(0),
) -> None:
    """Refuse unless audience, recipient and validity window all match."""
    if now.tzinfo is None:
        raise IdentityUnavailable("SAML check time must be timezone-aware")
    if provider.entity_id not in assertion.audiences:
        raise SamlAssertionRefused(
            SamlRefusalReason.WRONG_AUDIENCE, "assertion audience is not this provider"
        )
    if assertion.recipient != provider.acs_url:
        raise SamlAssertionRefused(
            SamlRefusalReason.WRONG_RECIPIENT, "assertion recipient is not the ACS URL"
        )
    now_utc = now.astimezone(UTC)
    if now_utc + clock_skew < assertion.not_before:
        raise SamlAssertionRefused(
            SamlRefusalReason.NOT_YET_VALID, "assertion is not yet valid"
        )
    if now_utc - clock_skew >= assertion.not_on_or_after:
        raise SamlAssertionRefused(SamlRefusalReason.EXPIRED, "assertion has expired")


class SamlSignatureVerifier(Protocol):
    """Injected port that verifies an assertion signature (crypto lives elsewhere)."""

    def verify(self, assertion: ParsedSamlAssertion, idp_certificate_pem: str) -> bool:
        """Return True only if the assertion is validly signed by the IdP certificate."""
        ...


def verify_assertion(
    provider: SamlServiceProvider,
    assertion: ParsedSamlAssertion,
    verifier: SamlSignatureVerifier | None,
    now: datetime,
    clock_skew: timedelta = timedelta(0),
) -> None:
    """Refuse without a verifier or on a bad signature, then check conditions."""
    if verifier is None:
        raise SamlAssertionRefused(
            SamlRefusalReason.SIGNATURE_VERIFIER_ABSENT,
            "no SAML signature verifier is configured",
        )
    if verifier.verify(assertion, provider.idp_certificate_pem) is not True:
        raise SamlAssertionRefused(
            SamlRefusalReason.INVALID_SIGNATURE, "assertion signature is invalid"
        )
    check_assertion_conditions(provider, assertion, now, clock_skew)
