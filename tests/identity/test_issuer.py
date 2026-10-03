"""Claim preparation tests; no signing authority is qualified by these fixtures."""

import pytest

from graph_os.identity.engine import IdentityUnavailable, Resolution
from graph_os.identity.issuer import IssuerSettings, claims_for

from .test_engine_resolution import eg_public_resolution_fixture, resolution_value

SETTINGS = IssuerSettings(
    "https://issuer.invalid", "fixture-audience", "fixture-tenant"
)


@pytest.mark.parametrize(
    ("source_expiry", "expected"), [(500_000, 400), (150_999, 150)]
)
def test_token_expiry_never_outlives_source(source_expiry, expected):
    claims = claims_for(
        Resolution.parse(resolution_value()),
        SETTINGS,
        source_expires_at_ms=source_expiry,
        now_ms=100_100,
    )
    assert claims["exp"] == expected
    assert claims["exp"] * 1000 <= source_expiry
    assert 0 < claims["exp"] - claims["iat"] <= 300
    assert claims["sub"] == claims["agent_id"] == "usr:fixture"
    assert claims["delegation"] == []
    assert claims["policy_version"] == "fixture-policy"
    assert claims["scope"] == "kg:read"
    assert claims["roles"] == ["reader"]
    assert "realm_access" not in claims


@pytest.mark.parametrize("expiry", [0, 99_000, 100_100, 100_999])
def test_expired_or_subsecond_source_cannot_issue(expiry):
    with pytest.raises(PermissionError):
        claims_for(
            Resolution.parse(resolution_value()),
            SETTINGS,
            source_expires_at_ms=expiry,
            now_ms=100_100,
        )


@pytest.mark.parametrize("expiry", [None, True, "200000", 200000.0, -1])
def test_missing_or_malformed_source_expiry_never_defaults(expiry):
    with pytest.raises(IdentityUnavailable):
        claims_for(
            Resolution.parse(resolution_value()),
            SETTINGS,
            source_expires_at_ms=expiry,
            now_ms=100_100,
        )


@pytest.mark.parametrize("field", ["tenant", "audience"])
def test_deployment_mismatch_refuses(field):
    value = resolution_value()
    value["request_context"][field] = "different"
    with pytest.raises(IdentityUnavailable):
        claims_for(
            Resolution.parse(value),
            SETTINGS,
            source_expires_at_ms=200_000,
            now_ms=100_000,
        )


def test_narrowed_claims_and_context_preserve_exact_empty_scopes():
    resolution = Resolution.parse(resolution_value()).narrow(())
    claims = claims_for(
        resolution, SETTINGS, source_expires_at_ms=200_000, now_ms=100_000
    )
    assert claims["scope"] == ""
    assert resolution.request_context["scopes"] == ()
    assert claims["roles"] == ["reader"]


@pytest.mark.parametrize("ttl", [True, 0, 301, "300", 300.0])
def test_ttl_does_not_coerce(ttl):
    with pytest.raises(ValueError):
        IssuerSettings(
            "https://issuer.invalid", "fixture-audience", "fixture-tenant", ttl
        )


def test_public_eg_context_does_not_supply_missing_credential_expiry():
    resolution = Resolution.from_reply(eg_public_resolution_fixture())
    with pytest.raises(IdentityUnavailable):
        claims_for(
            resolution,
            IssuerSettings("https://issuer.invalid", "engine", "tenant"),
            source_expires_at_ms=None,
            now_ms=100_000,
        )
