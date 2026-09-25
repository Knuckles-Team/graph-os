"""Doctor check ``identity_mode`` (IDM-16): exposed ``none`` and auto secrets are red."""

from __future__ import annotations

from dataclasses import replace

import pytest

from graph_os.deployment.doctor import CHECKS
from graph_os.deployment.doctor_identity import IdentitySettings, evaluate_identity
from graph_os.identity.modes import NONE_MODE_ACK

SECURED = IdentitySettings(
    profile="single-node-prod",
    mode="local",
    bind_host="0.0.0.0",
    none_ack=None,
    issuer="https://graph-os.test",
    tenant="homelab",
    auth_secret_configured=True,
)
DEMO = replace(SECURED, profile="tiny", mode="none", bind_host="127.0.0.1")


def test_the_check_is_registered() -> None:
    assert "identity_mode" in CHECKS


def test_a_secured_production_install_is_green() -> None:
    assert evaluate_identity(SECURED)["status"] == "ok"


def test_loopback_demo_is_a_warning_pointing_at_claim() -> None:
    verdict = evaluate_identity(DEMO)
    assert (
        verdict["status"] == "warn"
        and "graph-os-identity claim" in verdict["remediation"]
    )


@pytest.mark.parametrize(
    "exposed",
    [
        replace(DEMO, bind_host="0.0.0.0"),
        replace(DEMO, bind_host="0.0.0.0", none_ack=NONE_MODE_ACK),
        replace(DEMO, profile="enterprise"),
    ],
)
def test_exposed_none_is_red_even_when_acknowledged(exposed: IdentitySettings) -> None:
    assert evaluate_identity(exposed)["status"] == "fail"


def test_auto_generated_secret_is_red_on_production_and_a_warning_on_tiny() -> None:
    auto = replace(SECURED, auth_secret_configured=False)
    assert evaluate_identity(auto)["status"] == "fail"
    assert evaluate_identity(replace(auto, profile="tiny"))["status"] == "warn"


def test_production_needs_the_issuer_url_and_tenant() -> None:
    verdict = evaluate_identity(replace(SECURED, issuer=None))
    assert (
        verdict["status"] == "fail"
        and "GRAPHOS_IDENTITY_ISSUER" in verdict["remediation"]
    )


def test_settings_are_read_from_the_deployment() -> None:
    values = {"GRAPHOS_AUTH_NONE_EXPOSE": NONE_MODE_ACK, "GRAPHOS_IDENTITY_TENANT": "t"}

    class Config:
        deployment_profile = "tiny"
        host = "0.0.0.0"
        graph_service_auth_secret = ""

    settings = IdentitySettings.from_settings(
        Config(), lambda name, default: values.get(name, default)
    )
    assert settings.mode == "none" and settings.none_ack == NONE_MODE_ACK
    assert settings.tenant == "t" and settings.issuer is None
    assert settings.auth_secret_configured is False
