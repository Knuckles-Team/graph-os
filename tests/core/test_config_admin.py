"""GRAPHOS-DEPLOY-R009.1: GraphOS-owned hosting/deployment settings module."""

from __future__ import annotations

import inspect

import pytest

from graph_os.core import config_admin
from graph_os.core.config_admin import (
    HostingDeploymentSettings,
    InvalidHostingDeploymentSettings,
    load_hosting_deployment_settings,
)


@pytest.mark.spec("GRAPHOS-DEPLOY-R009.1")
def test_does_not_import_agent_runtime_configuration_internals() -> None:
    """The module resolves hosting/deployment settings without reaching into
    the agent runtime's monolithic configuration module."""
    import_lines = [
        line.strip()
        for line in inspect.getsource(config_admin).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert import_lines, "expected at least one import statement"
    assert not any("agent_utilities" in line for line in import_lines)


@pytest.mark.spec("GRAPHOS-DEPLOY-R009.1")
def test_resolves_defaults_from_empty_environment() -> None:
    settings = load_hosting_deployment_settings(env={})
    assert settings == HostingDeploymentSettings(
        host="0.0.0.0",
        port=8085,
        deploy_profile="dev",
        data_dir="/var/lib/graph-os",
    )


@pytest.mark.spec("GRAPHOS-DEPLOY-R009.1")
def test_resolves_overrides_from_environment() -> None:
    settings = load_hosting_deployment_settings(
        env={
            "GRAPHOS_HOST": "127.0.0.1",
            "GRAPHOS_PORT": "9000",
            "GRAPHOS_DEPLOY_PROFILE": "prod",
            "GRAPHOS_DATA_DIR": "/data/graph-os",
        }
    )
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.deploy_profile == "prod"
    assert settings.data_dir == "/data/graph-os"


@pytest.mark.spec("GRAPHOS-DEPLOY-R009.1")
@pytest.mark.parametrize(
    "env",
    [
        {"GRAPHOS_PORT": "not-a-number"},
        {"GRAPHOS_PORT": "0"},
        {"GRAPHOS_PORT": "70000"},
        {"GRAPHOS_DEPLOY_PROFILE": "staging"},
    ],
)
def test_rejects_invalid_settings(env: dict[str, str]) -> None:
    with pytest.raises(InvalidHostingDeploymentSettings):
        load_hosting_deployment_settings(env=env)
