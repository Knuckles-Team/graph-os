"""Tests for the GRAPHOS-HOST-R010 hosting/deployment settings model."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "graph_os" / "deployment" / "host_config.py"
)

# Loaded by file path rather than ``from graph_os.deployment.host_config import
# ...`` because importing the ``graph_os.deployment`` package also executes
# sibling submodules that import ``agent_utilities`` -- a dependency this
# development environment does not have installed, and one this module is
# written specifically not to need.
_spec = importlib.util.spec_from_file_location("host_config_under_test", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
host_config = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = host_config
_spec.loader.exec_module(host_config)

HostingDeploymentSettings = host_config.HostingDeploymentSettings
InvalidHostConfigError = host_config.InvalidHostConfigError
load_hosting_deployment_settings = host_config.load_hosting_deployment_settings


@pytest.mark.spec("GRAPHOS-HOST-R010")
def test_module_does_not_import_agent_utilities_config_internals() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(m.startswith("agent_utilities") for m in imported)


@pytest.mark.spec("GRAPHOS-HOST-R010")
def test_load_resolves_defaults_from_graphos_owned_env_vars() -> None:
    settings = load_hosting_deployment_settings(env={})
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8000
    assert settings.deployment_environment == "development"


def test_load_honors_graphos_owned_overrides() -> None:
    settings = load_hosting_deployment_settings(
        env={
            "GRAPH_OS_BIND_HOST": "127.0.0.1",
            "GRAPH_OS_BIND_PORT": "9100",
            "GRAPH_OS_PUBLIC_BASE_URL": "https://graphos.example.com",
            "GRAPH_OS_DEPLOYMENT_ENVIRONMENT": "production",
        }
    )
    assert settings.bind_port == 9100
    assert settings.deployment_environment == "production"


def test_refuses_an_out_of_range_port() -> None:
    with pytest.raises(InvalidHostConfigError):
        HostingDeploymentSettings(
            bind_host="0.0.0.0",
            bind_port=70000,
            public_base_url="http://localhost:8000",
            deployment_environment="development",
        )


def test_refuses_an_unrecognized_deployment_environment() -> None:
    with pytest.raises(InvalidHostConfigError):
        HostingDeploymentSettings(
            bind_host="0.0.0.0",
            bind_port=8000,
            public_base_url="http://localhost:8000",
            deployment_environment="prod-ish",
        )
