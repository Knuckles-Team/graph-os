"""Tests for ``scripts/check_connector_count_pins.py`` (GRAPHOS-FLEET-R023.1).

Mirrors ``tests/test_check_core_skills.py``'s convention: run the script
exactly as a caller would, as a subprocess, never by importing it as a mock.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from graph_os.fleet.connector_count_pins import (
    CountPinSite,
    check_connector_count_pins,
)

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "check_connector_count_pins.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pure_checker_agrees_on_matching_sites() -> None:
    sites = [CountPinSite(name="genesis", location="genesis.yaml", declared_count=5)]
    assert check_connector_count_pins(5, sites) == []


def test_pure_checker_flags_each_mismatching_site() -> None:
    sites = [
        CountPinSite(name="genesis", location="genesis.yaml", declared_count=5),
        CountPinSite(
            name="bundle-catalog", location="bundle-catalog.json", declared_count=3
        ),
    ]
    mismatches = check_connector_count_pins(5, sites)
    assert len(mismatches) == 1
    assert mismatches[0].site == "bundle-catalog"
    assert mismatches[0].declared_count == 3
    assert mismatches[0].live_count == 5


def test_script_exits_zero_when_every_site_agrees() -> None:
    result = _run("--live-count", "5", "--site", "genesis=genesis.yaml=5")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_script_refuses_on_a_deliberate_mismatch_fixture() -> None:
    """A real refusal test through the real CLI entry point."""
    result = _run(
        "--live-count",
        "5",
        "--site",
        "genesis=genesis.yaml=5",
        "--site",
        "bundle-catalog=bundle-catalog.json=3",
    )
    assert result.returncode == 1
    assert "MISMATCH" in result.stderr
    assert "bundle-catalog" in result.stderr
    assert "declares 3" in result.stderr
    assert "live count is 5" in result.stderr


def test_script_rejects_a_malformed_site_argument() -> None:
    result = _run("--live-count", "5", "--site", "not-enough-parts")
    assert result.returncode == 2
    assert "must be name=location=declared_count" in result.stderr
