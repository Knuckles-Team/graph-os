"""Pure assertion-condition checks for the .2.1 slice: R012.2.1."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.saml import (
    ParsedSamlAssertion,
    SamlAssertionRefused,
    SamlRefusalReason,
    SamlServiceProvider,
    check_assertion_conditions,
)

_CERT = "-----BEGIN CERTIFICATE-----\nMII...\n-----END CERTIFICATE-----"
_ACS = "https://graph-os.example.org/saml/acs"
_T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
_PROVIDER = SamlServiceProvider("urn:graph-os:sp", _ACS, _CERT)


def _assertion(**overrides: Any) -> ParsedSamlAssertion:
    fields: dict[str, Any] = {
        "assertion_id": "_a1",
        "issuer": "https://idp.example.org",
        "subject": "alice",
        "audiences": ("urn:graph-os:sp",),
        "recipient": _ACS,
        "not_before": _T0 - timedelta(minutes=1),
        "not_on_or_after": _T0 + timedelta(minutes=5),
    }
    fields.update(overrides)
    return ParsedSamlAssertion(**fields)


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.1")
def test_valid_assertion_passes() -> None:
    check_assertion_conditions(_PROVIDER, _assertion(), _T0)


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.1")
@pytest.mark.parametrize(
    ("overrides", "now", "reason"),
    [
        ({"audiences": ("urn:other",)}, _T0, SamlRefusalReason.WRONG_AUDIENCE),
        (
            {"recipient": "https://evil.example/acs"},
            _T0,
            SamlRefusalReason.WRONG_RECIPIENT,
        ),
        ({}, _T0 - timedelta(minutes=2), SamlRefusalReason.NOT_YET_VALID),
        ({}, _T0 + timedelta(minutes=5), SamlRefusalReason.EXPIRED),
    ],
)
def test_condition_failures_are_refused_with_typed_reason(
    overrides: dict[str, Any], now: datetime, reason: SamlRefusalReason
) -> None:
    with pytest.raises(SamlAssertionRefused) as info:
        check_assertion_conditions(_PROVIDER, _assertion(**overrides), now)
    assert info.value.reason is reason


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.1")
def test_clock_skew_tolerates_small_drift() -> None:
    check_assertion_conditions(
        _PROVIDER,
        _assertion(),
        _T0 + timedelta(minutes=5, seconds=10),
        clock_skew=timedelta(seconds=30),
    )


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.1")
@pytest.mark.parametrize(
    "overrides",
    [
        {"assertion_id": ""},
        {"audiences": ()},
        {"not_before": datetime(2026, 10, 10, 12, 0)},
        {"not_on_or_after": _T0 - timedelta(minutes=5)},
    ],
)
def test_malformed_assertion_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(IdentityUnavailable):
        _assertion(**overrides)


@pytest.mark.spec("GRAPHOS-IDENTITY-R012.2.1")
def test_naive_check_time_is_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        check_assertion_conditions(_PROVIDER, _assertion(), datetime(2026, 10, 10, 12))
