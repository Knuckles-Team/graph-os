"""Spec-bound refusal tests for OIDC ID-token claim checks."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.oidc import (
    OidcRefusalReason,
    OidcTokenRefused,
    check_id_token_claims,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _claims(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": "https://idp",
        "aud": "client",
        "exp": NOW.timestamp() + 60,
        "nonce": "n1",
    }
    base.update(over)
    return base


def _check(claims: dict[str, Any], **kw: Any) -> None:
    check_id_token_claims(
        claims, issuer="https://idp", audience="client", nonce="n1", now=NOW, **kw
    )


def _reason(claims: dict[str, Any], **kw: Any) -> OidcRefusalReason:
    with pytest.raises(OidcTokenRefused) as ei:
        _check(claims, **kw)
    return ei.value.reason


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_valid_claims_pass_with_string_or_list_audience() -> None:
    _check(_claims())
    _check(_claims(aud=["other", "client"]))


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_wrong_issuer_refused() -> None:
    assert _reason(_claims(iss="https://evil")) is OidcRefusalReason.WRONG_ISSUER


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_wrong_or_missing_audience_refused() -> None:
    assert _reason(_claims(aud="other")) is OidcRefusalReason.WRONG_AUDIENCE
    assert _reason(_claims(aud=None)) is OidcRefusalReason.WRONG_AUDIENCE


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_expired_and_missing_expiry_refused() -> None:
    past = _claims(exp=NOW.timestamp() - 1)
    assert _reason(past) is OidcRefusalReason.EXPIRED
    assert _reason(_claims(exp="soon")) is OidcRefusalReason.MISSING_EXPIRY
    assert _reason(_claims(exp=True)) is OidcRefusalReason.MISSING_EXPIRY


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_clock_skew_tolerates_recent_expiry() -> None:
    _check(_claims(exp=NOW.timestamp() - 5), clock_skew=timedelta(seconds=30))


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_nonce_mismatch_refused() -> None:
    assert _reason(_claims(nonce="x")) is OidcRefusalReason.NONCE_MISMATCH


@pytest.mark.spec("GRAPHOS-IDENTITY-R009.2.2")
def test_naive_time_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        check_id_token_claims(
            _claims(),
            issuer="https://idp",
            audience="client",
            nonce="n1",
            now=datetime(2026, 10, 10),
        )
