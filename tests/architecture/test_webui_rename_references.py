"""GRAPHOS-HOST-R003: web UI rename references."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.spec("GRAPHOS-HOST-R003")
def test_webui_extra_source_and_release_checkout_reference_agent_webui() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["webui"]
    assert any(dep.startswith("agent-webui") for dep in extra)
    assert "agent-webui" in data["tool"]["uv"]["sources"]
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "repository: Knuckles-Team/agent-webui" in workflow
