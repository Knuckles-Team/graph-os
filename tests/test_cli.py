"""Wiring test: the `graph-os` console entry point does something real.

Proves the entry point declared in `pyproject.toml` (`[project.scripts]
graph-os = "graph_os.cli:main"`) is actually reachable and reports the
installed version — not merely importable in isolation (Wire-First: a
wiring test drives the real entrypoint, not the component in a vacuum).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from graph_os import __version__
from graph_os.cli import build_parser, main


def test_build_parser_describes_the_target_composition() -> None:
    parser = build_parser()
    assert "graph-os" in parser.prog
    assert "RF-ADR-009" in parser.description


def test_version_flag_reports_the_installed_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_help_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])
    assert exit_code == 0
    assert "graph-os" in capsys.readouterr().out


def test_console_module_entrypoint_runs_out_of_process() -> None:
    """Drive the SAME code path the installed `graph-os` console script uses,
    out of process, so this proves the packaging wiring too (not just the
    Python function)."""
    result = subprocess.run(
        [sys.executable, "-m", "graph_os.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout
