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
        if "uv sync --frozen --extra test" in step.get("run", "")
    )
    return steps[:sync_index]


def _sync_command() -> str:
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return next(
        step["run"]
        for step in workflow["jobs"]["gates"]["steps"]
        if "uv sync --frozen --extra test" in step.get("run", "")
    )


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
        step = next(item for item in steps if item.get("with", {}).get("path") == path)
        assert len(step["with"]["ref"]) == 40
        assert step["with"]["persist-credentials"] is False


def test_scanner_job_installs_pinned_uv_before_uvx() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["scanner-quality"]["steps"]
    uv_index = next(
        index
        for index, step in enumerate(steps)
        if "astral-sh/setup-uv@" in step.get("uses", "")
    )
    scanner_index = next(
        index
        for index, step in enumerate(steps)
        if "pre-commit run" in step.get("run", "")
    )

    assert uv_index < scanner_index
    assert steps[uv_index]["with"]["version"] == "0.11.7"


def test_webui_checkout_uses_reachable_published_commit() -> None:
    steps = _steps_before_sync()
    checkout = next(
        step
        for step in steps
        if step.get("with", {}).get("repository") == "Knuckles-Team/agent-webui"
    )

    assert checkout["with"]["ref"] == "a25478b4b1e892c5df8c5c79113ea37c89578053"


def test_release_workflow_pins_published_generated_contract_heads() -> None:
    steps = _steps_before_sync()
    refs = {
        step["with"]["repository"]: step["with"]["ref"]
        for step in steps
        if step.get("with", {}).get("repository")
    }

    assert refs["Knuckles-Team/agent-connector-sdk"] == (
        "da1757b998698d2e8d5c60c4eca9aea18d9baf8d"
    )
    assert refs["Knuckles-Team/epistemic-graph"] == (
        "49d63da5396fef7482fc3617df3f90a836661722"
    )


def test_release_workflow_uses_pinned_epistemic_graph_contract_overlay() -> None:
    command = _sync_command()

    assert (
        "uv sync --frozen --extra test --no-install-package epistemic-graph" in command
    )
    assert "PYTHONPATH=$eg_source" in command
    assert 'git -C "$eg_source" rev-parse HEAD' in command
    assert "49d63da5396fef7482fc3617df3f90a836661722" in command
    assert "epistemic_graph.__file__" in command
    assert "source_ingestion.SourceCheckpoint" in command
    assert "storage.send_agent_component_content" in command


def test_scanner_versions_receive_distinct_argv_and_remain_advisory() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    scanner = workflow["jobs"]["scanner-quality"]
    command = next(
        step["run"]
        for step in scanner["steps"]
        if "pipelines-hook scanner-versions" in step.get("run", "")
    )

    assert "pipelines-hook scanner-versions cccc kiss dupehound jscpd" in command
    assert "['cccc'" not in command
    assert scanner["continue-on-error"] is True
    assert workflow["jobs"]["build"]["needs"] == ["gates"]
