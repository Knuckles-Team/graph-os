"""CI must materialize every locked path source before frozen synchronization."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def _declared_paths() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        value["path"]
        for value in pyproject["tool"]["uv"]["sources"].values()
        if "path" in value
    }


def _locked_paths() -> set[str]:
    locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {
        package["source"]["editable"]
        for package in locked["package"]
        if "editable" in package.get("source", {})
    }


def _steps_before_sync() -> list[dict[str, object]]:
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["gates"]["steps"]
    sync_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "uv sync --frozen --extra test"
    )
    return steps[:sync_index]


def test_release_workflow_materializes_uv_path_sources_before_sync() -> None:
    required = _declared_paths()
    steps = _steps_before_sync()
    provisioned = {
        step.get("with", {}).get("path")
        for step in steps
        if step.get("uses", "").startswith("actions/checkout@")
    }

    assert required <= provisioned
    provisioning_plan = yaml.safe_dump(steps)
    assert all(path in provisioning_plan for path in _locked_paths())
    for path in required:
        step = next(
            item
            for item in steps
            if item.get("with", {}).get("path") == path
        )
        assert len(step["with"]["ref"]) == 40
        assert step["with"]["persist-credentials"] is False
