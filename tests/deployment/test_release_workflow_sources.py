"""Structural contracts of the release workflow, derived from their sources of truth.

The workflow's own pins (sibling commits, tool versions) are the single place a
revision is recorded; these tests check that each pin is immutable and wired in
the right order, never that it equals a copy kept here.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
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


_SHELL_VAR = re.compile(r'^\s*(\w+)="\$GITHUB_WORKSPACE/([^"]+)"\s*$', re.MULTILINE)
_SYMLINK = re.compile(r'ln -s "\$(\w+)" "\$(\w+)/([^"]+)"')


def _materialized_paths(steps: list[dict[str, Any]], checkouts: set[str]) -> set[str]:
    """Nested editable paths the workflow backs with a symlink, not a checkout.

    ``agent-connector-sdk`` is declared as an editable path source by both
    graph-os and agent-utilities (which needs it directly too); ``agent-utilities``
    likewise by both graph-os and agent-webui. Both declarations resolve the
    same real directory, so uv's resolver may record either project's
    relative path in uv.lock for the same package -- this repository's own
    "Materialize nested pinned source paths" step symlinks the nested
    location to the already-checked-out flat one so `uv sync` succeeds
    either way. Reading that step's own shell variables (rather than
    hard-coding the paths here a second time) keeps this test correct when
    the step's variable names change, and additionally verifies every
    symlink's target is itself one of the checked-out paths -- a dangling
    symlink would otherwise go unnoticed.
    """
    script = steps[_step_index(steps, "ln -s")]["run"]
    var_paths = dict(_SHELL_VAR.findall(script))

    materialized: set[str] = set()
    for target_var, link_dir_var, suffix in _SYMLINK.findall(script):
        target = var_paths[target_var]
        assert target in checkouts, (
            f"ln -s target ${target_var} ({target}) is not one of the "
            "checked-out paths -- it would dangle"
        )
        materialized.add(f"{var_paths[link_dir_var]}/{suffix}")
    return materialized


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
    # "." is this checkout itself (the job's initial actions/checkout, always
    # present); every other locked editable path must be either a dedicated
    # checkout above or a symlink the workflow creates onto one.
    provisioned = {".", *checkouts, *_materialized_paths(before_sync, set(checkouts))}
    assert _locked_paths() <= provisioned
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


def test_scanner_versions_receive_distinct_argv_and_the_job_blocks() -> None:
    scanner = _workflow()["jobs"]["scanner-quality"]
    command = next(
        step["run"]
        for step in scanner["steps"]
        if "pipelines-hook scanner-versions" in step.get("run", "")
    )

    assert "pipelines-hook scanner-versions cccc kiss dupehound jscpd" in command
    assert "['cccc'" not in command
    assert "continue-on-error" not in scanner


def _scanner_step(name: str) -> dict[str, Any]:
    return next(
        step
        for step in _workflow()["jobs"]["scanner-quality"]["steps"]
        if step.get("name") == name
    )


def test_scanner_cache_retains_installation_and_all_checks_before_save() -> None:
    scanner = _workflow()["jobs"]["scanner-quality"]
    steps = scanner["steps"]
    restore = _scanner_step("Restore scanner toolchain")
    provision = _scanner_step("Provision pinned scanner toolchain")
    checks = _scanner_step(
        "Shared scanner hooks (versions, censuses, clone differentials)"
    )
    save = _scanner_step("Save verified scanner toolchain")

    assert steps.index(restore) < steps.index(provision) < steps.index(checks)
    assert steps.index(checks) < steps.index(save)
    # Both cold misses and warm hits take the same installer/verification path.
    assert "if" not in provision
    assert "if" not in checks
    assert provision["run"].startswith("bash scripts/install_scanners.sh ")
    assert "$RUNNER_TEMP/graph-os-scanners" in provision["run"]
    assert set(_manual_hooks_named(checks["run"])) == {
        "complexity-census",
        "kiss-census",
        "dupehound-changed",
        "jscpd-differential",
        "jscpd-census",
    }
    assert save["if"] == "success() && steps.scanner-cache.outputs.cache-hit != 'true'"
    for step in (restore, provision, checks, save):
        assert "continue-on-error" not in step
    assert _workflow()["permissions"] == {"contents": "read"}
    assert "permissions" not in scanner


def test_scanner_cache_is_exact_and_contains_only_the_scanner_prefix() -> None:
    restore = _scanner_step("Restore scanner toolchain")
    save = _scanner_step("Save verified scanner toolchain")
    identity = _scanner_step("Resolve scanner cache identity")

    assert (
        restore["with"]
        == save["with"]
        == {
            "path": "${{ runner.temp }}/graph-os-scanners",
            "key": "${{ steps.scanner-cache-identity.outputs.key }}",
        }
    )
    for operation, step in (("restore", restore), ("save", save)):
        action, _, revision = step["uses"].partition("@")
        assert action == f"actions/cache/{operation}"
        assert SHA.fullmatch(revision)
    assert identity["env"]["SCANNER_RECIPE"] == (
        "${{ hashFiles('scripts/install_scanners.sh') }}"
    )


@pytest.fixture
def scanner_identity_environment(tmp_path: Path) -> dict[str, str]:
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("rustc", "cargo"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\n[ "$FAIL_TOOL" != "' + name + '" ] || exit 23\n'
            'printf "%s\\n" "$TEST_' + name.upper() + '_IDENTITY"\n'
        )
        command.chmod(0o755)
    return {
        **os.environ,
        **_workflow()["jobs"]["scanner-quality"]["env"],
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
        "ImageOS": "ubuntu24",
        "ImageVersion": "20261001.1.0",
        "SCANNER_RECIPE": "installer-and-provider-inputs",
        "TEST_RUSTC_IDENTITY": "rustc fixture compiler",
        "TEST_CARGO_IDENTITY": "cargo fixture compiler",
        "FAIL_TOOL": "",
        "RUSTFLAGS": "",
        "CARGO_ENCODED_RUSTFLAGS": "",
    }


def _scanner_cache_identity(env: dict[str, str]) -> tuple[int, str]:
    output = Path(env["GITHUB_OUTPUT"])
    output.unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", "-c", _scanner_step("Resolve scanner cache identity")["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode, output.read_text() if output.exists() else ""


@pytest.mark.parametrize(
    "changed_input",
    [
        "RUNNER_OS",
        "RUNNER_ARCH",
        "ImageOS",
        "ImageVersion",
        "SCANNER_RECIPE",
        "TEST_RUSTC_IDENTITY",
        "TEST_CARGO_IDENTITY",
        "CARGO_BUILD_JOBS",
        "CARGO_PROFILE_RELEASE_DEBUG",
        "RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
    ],
)
def test_scanner_cache_identity_changes_with_build_inputs(
    scanner_identity_environment: dict[str, str], changed_input: str
) -> None:
    env = scanner_identity_environment
    code, before = _scanner_cache_identity(env)
    assert code == 0
    assert re.fullmatch(r"key=graph-os-scanners-v1-[0-9a-f]{64}\n", before)
    assert _scanner_cache_identity(env) == (0, before)
    code, after = _scanner_cache_identity({**env, changed_input: "changed"})
    assert code == 0
    assert after != before


@pytest.mark.parametrize("tool", ["rustc", "cargo"])
def test_scanner_cache_identity_fails_closed_without_a_compiler(
    scanner_identity_environment: dict[str, str], tool: str
) -> None:
    assert _scanner_cache_identity(
        {**scanner_identity_environment, "FAIL_TOOL": tool}
    ) == (23, "")
