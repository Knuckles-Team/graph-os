"""Structural contracts of the release workflow, derived from their sources of truth.

The workflow's own pins (sibling commits, tool versions) are the single place a
revision is recorded; these tests check that each pin is immutable and wired in
the right order, never that it equals a copy kept here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]
SHA = re.compile(r"[0-9a-f]{40}")


def _workflow() -> dict[str, Any]:
    path = ROOT / ".github" / "workflows" / "release.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _gate_steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["gates"]["steps"]


def _step_index(steps: list[dict[str, Any]], needle: str) -> int:
    return next(i for i, step in enumerate(steps) if needle in step.get("run", ""))


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


def _manual_hook_ids() -> set[str]:
    config = yaml.safe_load(
        (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    return {
        hook["id"]
        for repo in config["repos"]
        for hook in repo["hooks"]
        if "manual" in hook.get("stages", [])
    }


def _manual_hooks_named(commands: str) -> set[str]:
    named = set(
        re.findall(r"pre-commit run ([a-z][a-z-]*) --hook-stage manual", commands)
    )
    for group in re.findall(r"for hook in ([a-z -]+); do", commands):
        named |= set(group.split())
    return named


def test_release_workflow_materializes_uv_path_sources_before_sync() -> None:
    steps = _gate_steps()
    before_sync = steps[: _step_index(steps, "uv sync --frozen --extra test")]
    checkouts = {
        step["with"]["path"]: step
        for step in before_sync
        if step.get("uses", "").startswith("actions/checkout@")
        and "path" in step.get("with", {})
    }

    assert _declared_paths() <= set(checkouts)
    provisioning_plan = yaml.safe_dump(before_sync)
    assert all(path in provisioning_plan for path in _locked_paths())
    for step in checkouts.values():
        assert SHA.fullmatch(step["with"]["ref"]), step["with"]
        assert step["with"]["persist-credentials"] is False


def test_every_uvx_runs_after_a_pinned_uv() -> None:
    for name, job in _workflow()["jobs"].items():
        steps = job["steps"]
        uvx = [i for i, step in enumerate(steps) if "uvx " in step.get("run", "")]
        if not uvx:
            continue
        setup = next(
            i
            for i, step in enumerate(steps)
            if "astral-sh/setup-uv@" in step.get("uses", "")
        )
        assert setup < min(uvx), name
        assert re.fullmatch(r"\d+\.\d+\.\d+", steps[setup]["with"]["version"]), name


def test_one_python_version_is_pinned_for_every_job() -> None:
    versions = {
        step["with"]["python-version"]
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if "actions/setup-python@" in step.get("uses", "")
    }

    # scripts/bootstrap.sh reads this same pin.
    assert len(versions) == 1
    assert re.fullmatch(r"3\.\d+", versions.pop())


def test_gates_run_the_repository_pre_commit_configuration() -> None:
    steps = _gate_steps()
    commands = "\n".join(step.get("run", "") for step in steps)

    assert "pre-commit run --all-files" in commands
    assert "pre-commit run --hook-stage pre-push" in commands
    assert {"pytest", "mypy-env"} <= _manual_hooks_named(commands)
    # The full suite runs only after the environment it needs is synced.
    assert _step_index(steps, "uv sync --frozen") < _step_index(steps, "pytest")


def test_every_manual_hook_ci_names_is_defined() -> None:
    commands = "\n".join(
        step.get("run", "")
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
    )

    assert _manual_hooks_named(commands) <= _manual_hook_ids()


def test_epistemic_graph_is_overlaid_from_the_pinned_source() -> None:
    steps = _gate_steps()
    command = steps[_step_index(steps, "uv sync --frozen --extra test")]["run"]

    assert "--no-install-package epistemic-graph" in command
    assert "PYTHONPATH=$eg_source" in command
    assert 'python "$eg_source/scripts/build_numeric_kernel.py"' in command
    assert "epistemic_graph.__file__" in command


def test_kernel_build_cache_covers_the_target_dir_cargo_uses() -> None:
    steps = _gate_steps()
    cache = next(
        step for step in steps if "Swatinem/rust-cache@" in step.get("uses", "")
    )
    workspace, _, target = cache["with"]["workspaces"].partition(" -> ")
    kernel_checkout = next(
        step
        for step in steps
        if step.get("with", {}).get("repository") == "Knuckles-Team/epistemic-graph"
    )

    assert workspace == kernel_checkout["with"]["path"]
    # build_numeric_kernel.py always builds into <checkout>/target-isolated.
    assert target == "target-isolated"
    assert steps.index(cache) < _step_index(steps, "build_numeric_kernel.py")
    env = _workflow()["jobs"]["gates"]["env"]
    assert env["CARGO_BUILD_JOBS"] == "4"
    assert env["CARGO_PROFILE_RELEASE_DEBUG"] == "0"


def test_release_dependency_readiness_blocks_tag_build_only() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["gates"]["steps"]
    checkout = next(
        step
        for step in steps
        if step.get("name") == "Checkout pinned release-readiness implementation"
    )
    readiness = next(
        step for step in steps if step.get("name") == "Release dependency readiness"
    )

    assert checkout["with"]["repository"] == "Knuckles-Team/repository-manager"
    assert SHA.fullmatch(checkout["with"]["ref"])
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["if"] == readiness["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert steps.index(checkout) < steps.index(readiness)
    assert "dependency-readiness --hook-stage manual" in readiness["run"]
    assert workflow["jobs"]["build"]["needs"] == ["gates"]


def test_scanner_versions_receive_distinct_argv_and_remain_advisory() -> None:
    scanner = _workflow()["jobs"]["scanner-quality"]
    command = next(
        step["run"]
        for step in scanner["steps"]
        if "pipelines-hook scanner-versions" in step.get("run", "")
    )

    assert "pipelines-hook scanner-versions cccc kiss dupehound jscpd" in command
    assert "['cccc'" not in command
    assert scanner["continue-on-error"] is True
