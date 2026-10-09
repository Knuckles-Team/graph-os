"""The release-qualification runbook stays backed by real checks
(GRAPHOS-DEPLOY-R002).

Each stage in ``docs/release-qualification-runbook.md`` names an automated
check or script. This test proves, for every stage that cites a check this
repository owns, that the named check still exists: a renamed or removed
``agent-utilities-doctor`` check, console script, or test directory would
fail this test rather than silently leaving the runbook stale.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "release-qualification-runbook.md"


def _runbook_text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_runbook_exists_and_is_linked_from_mkdocs() -> None:
    assert RUNBOOK.is_file()
    nav = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "release-qualification-runbook.md" in nav


def test_runbook_covers_every_required_component() -> None:
    text = _runbook_text()
    for component in (
        "Graph engine",
        "Connector SDK",
        "Agent runtime",
        "GraphOS itself",
        "Web UI",
        "Connector package fleet",
        "Production redeployment",
    ):
        assert component in text, f"runbook is missing a stage for: {component}"


def test_runbook_cites_real_doctor_checks() -> None:
    from graph_os.deployment.doctor import _LIVE_CHECK_NAMES, CHECKS

    text = _runbook_text()
    for check_name in ("mcp_fleet", "mcp_fleet_secrets"):
        assert check_name in text
        assert check_name in CHECKS, f"{check_name} is no longer a doctor check"
    assert "mcp_fleet" in _LIVE_CHECK_NAMES


def test_runbook_cites_real_console_scripts() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = pyproject["project"]["scripts"]
    text = _runbook_text()
    for script_name in (
        "graph-os-release-canary",
        "graph-os-production-ops",
        "agent-utilities-doctor",
    ):
        assert script_name in text
        assert script_name in scripts, f"{script_name} is no longer a console script"


def test_runbook_cites_a_real_test_directory() -> None:
    assert "tests/deployment" in _runbook_text()
    assert (REPO_ROOT / "tests" / "deployment").is_dir()
    assert any((REPO_ROOT / "tests" / "deployment").glob("test_*.py"))
