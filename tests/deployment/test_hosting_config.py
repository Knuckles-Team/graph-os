"""Hosting topology belongs to the GraphOS deployment boundary."""

import pytest

from graph_os.deployment.config import HostingProfileError, resolve_deployment_profile


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
