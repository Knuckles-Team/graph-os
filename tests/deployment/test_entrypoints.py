"""Wiring checks for the deployment extraction's direct console scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from graph_os.deployment import release_canary


def _project_scripts() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["scripts"]


def test_host_deployment_scripts_point_to_graph_os_modules() -> None:
    scripts = _project_scripts()
    assert scripts == {
        "agent-utilities-doctor": "graph_os.deployment.doctor:main",
        "agent-utilities-venv": "graph_os.deployment.venv_sync:main",
        "graph-os": "graph_os.cli:main",
        "graph-os-production-ops": "graph_os.deployment.production_ops:main",
        "graph-os-release-canary": "graph_os.deployment.release_canary:main",
        "setup-config": "graph_os.deployment.cli:main",
    }


def test_release_canary_checks_the_same_direct_entrypoints() -> None:
    assert release_canary._ENTRY_POINTS == {
        "graph-os": "graph_os.cli:main",
        "agent-utilities-doctor": "graph_os.deployment.doctor:main",
    }
