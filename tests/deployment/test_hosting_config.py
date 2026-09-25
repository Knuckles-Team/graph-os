"""Hosting topology belongs to the GraphOS deployment boundary."""

import pytest

from graph_os.deployment.config import (
    HostingProfileError,
    app_profile,
    configured_profiles,
    is_production_posture,
    resolve_deployment_profile,
)


def test_posture_reads_graphos_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", " Production ")
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "enterprise")
    assert configured_profiles() == ("enterprise", "production")
    assert is_production_posture(configured_profiles()[1])
    assert app_profile(None) == "dev"


def test_development_uses_tiny_when_topology_is_unset() -> None:
    assert resolve_deployment_profile(None, "dev") == "tiny"


def test_production_requires_an_explicit_topology() -> None:
    with pytest.raises(HostingProfileError) as caught:
        resolve_deployment_profile(None, "production")
    assert caught.value.code == "deployment_profile_required"


def test_invalid_topology_is_rejected_without_echoing_it() -> None:
    with pytest.raises(HostingProfileError) as caught:
        resolve_deployment_profile("secret-value", "dev")
    assert caught.value.code == "deployment_profile_invalid"
    assert "secret-value" not in str(caught.value)


def test_explicit_production_topology() -> None:
    assert resolve_deployment_profile("enterprise", "production") == "enterprise"
