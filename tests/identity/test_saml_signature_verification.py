"""Signature verification through an injected port (GRAPHOS-IDENTITY-R012.2.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graph_os.identity.saml import (
    ParsedSamlAssertion,
    SamlAssertionRefused,
    SamlRefusalReason,
    SamlServiceProvider,
    verify_assertion,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
PROVIDER = SamlServiceProvider(
    entity_id="https://sp.example/meta",
    acs_url="https://sp.example/acs",
    idp_certificate_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----",
)


def _assertion(audience: str = "https://sp.example/meta") -> ParsedSamlAssertion:
    return ParsedSamlAssertion(
        assertion_id="_a1",
        issuer="https://idp.example",
        subject="alice",
        audiences=(audience,),
        recipient=PROVIDER.acs_url,
        not_before=NOW - timedelta(minutes=1),
        not_on_or_after=NOW + timedelta(minutes=5),
    )


class FakeVerifier:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[str] = []

    def verify(self, assertion: ParsedSamlAssertion, idp_certificate_pem: str) -> bool:
        self.calls.append(idp_certificate_pem)
        return self.result


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.2")
def test_absent_verifier_is_refused() -> None:
    with pytest.raises(SamlAssertionRefused) as exc:
        verify_assertion(PROVIDER, _assertion(), None, NOW)
    assert exc.value.reason is SamlRefusalReason.SIGNATURE_VERIFIER_ABSENT


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.2")
def test_invalid_signature_is_refused_before_conditions() -> None:
    fake = FakeVerifier(False)
    with pytest.raises(SamlAssertionRefused) as exc:
        verify_assertion(PROVIDER, _assertion("https://other"), fake, NOW)
    assert exc.value.reason is SamlRefusalReason.INVALID_SIGNATURE
    assert fake.calls == [PROVIDER.idp_certificate_pem]


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.2")
def test_valid_signature_then_conditions_are_checked() -> None:
    verify_assertion(PROVIDER, _assertion(), FakeVerifier(True), NOW)
    with pytest.raises(SamlAssertionRefused) as exc:
        verify_assertion(PROVIDER, _assertion("https://other"), FakeVerifier(True), NOW)
    assert exc.value.reason is SamlRefusalReason.WRONG_AUDIENCE
