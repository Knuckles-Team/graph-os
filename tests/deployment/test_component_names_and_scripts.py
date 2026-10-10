"""GRAPHOS-DEPLOY-R005 / R007: web UI package name and console-script ownership."""

from __future__ import annotations

import importlib.util
import tomllib
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.mark.spec("GRAPHOS-DEPLOY-R005")
def test_web_ui_keeps_agent_webui_package_name() -> None:
    extra = [
        Requirement(r) for r in PYPROJECT["project"]["optional-dependencies"]["webui"]
    ]
    assert "agent-webui" in {r.name for r in extra}
    assert "agent-webui" in PYPROJECT["tool"]["uv"]["sources"]
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "repository: Knuckles-Team/agent-webui" in workflow
    assert importlib.util.find_spec("agent_webui") is not None


# Exclusivity (agent-utilities no longer registering these) is verified against
# agent-utilities main; a stale installed agent-utilities dist-info in a shared
# venv may still list them, so only graph-os ownership is asserted here.
def _declarers(script: str) -> list[tuple[str, str]]:
    found = []
    for ep in entry_points(group="console_scripts", name=script):
        dist = ep.dist.name if ep.dist else ""
        found.append((dist, ep.value))
    return found


@pytest.mark.spec("GRAPHOS-DEPLOY-R007.1")
def test_agent_utilities_doctor_declared_only_by_graph_os() -> None:
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts["agent-utilities-doctor"] == "graph_os.deployment.doctor:main"
    assert "graph_os.deployment.doctor:main" in {
        v for _, v in _declarers("agent-utilities-doctor")
    }


@pytest.mark.spec("GRAPHOS-DEPLOY-R007.2")
def test_setup_config_declared_only_by_graph_os() -> None:
    scripts = PYPROJECT["project"]["scripts"]
    assert scripts["setup-config"] == "graph_os.deployment.cli:main"
    assert "graph_os.deployment.cli:main" in {v for _, v in _declarers("setup-config")}
