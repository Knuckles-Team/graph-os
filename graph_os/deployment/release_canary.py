"""Bounded, privacy-safe canary for an exact local GraphOS release.

The canary deliberately does not start GraphOS or the Epistemic Graph server.  It
proves that the promoted Python environment exposes the current console entry
points, the packaged server binary, and the folded native numeric kernel. The
release promoter separately proves that no GraphOS or engine process exists
before or after this command. Fleet readiness is intentionally not reconstructed
from a file here: GraphOS startup reads and verifies the live EG-backed catalog
before serving.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from typing import Any

_ENTRY_POINTS = {
    # Certify the native serving composition, not the removed informational
    # shell and never an AU compatibility alias.
    "graph-os": "graph_os.mcp_server.server:mcp_server",
    "agent-utilities-doctor": "graph_os.deployment.doctor:main",
}


def _entry_points_ready() -> bool:
    discovered = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="console_scripts")
        if entry.name in _ENTRY_POINTS
    }
    return discovered == _ENTRY_POINTS


def _engine_binary_ready() -> bool:
    candidate = Path(sys.executable).with_name("epistemic-graph-server")
    try:
        metadata = candidate.lstat()
    except OSError:
        return False
    return (
        candidate.is_file()
        and not candidate.is_symlink()
        and bool(metadata.st_mode & 0o111)
    )


def _numeric_kernel_ready() -> bool:
    try:
        from agent_utilities import numeric as utilities_numeric

        engine_numeric = importlib.import_module("epistemic_graph.numeric")
        return bool(
            getattr(engine_numeric, "__kernel__", None) == "eg-numeric"
            and utilities_numeric.xp.sum([1.0, 2.0, 3.0]) == 6.0
        )
    except Exception:  # noqa: BLE001 - the result is intentionally aggregate-only
        return False


def _leading_major_version(text: str) -> int | None:
    match = re.match(r"\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _declared_fastmcp_major() -> int | None:
    for requirement in importlib.metadata.requires("graph-os") or ():
        name, _, specifier = requirement.partition(">=")
        if name.strip() != "fastmcp":
            continue
        return _leading_major_version(specifier)
    return None


def _served_fastmcp_matches_declared() -> bool:
    """Prove the promoted environment serves the FastMCP major it declares.

    GraphOS's MCP bridge is a FastMCP 4 composition. A floor that resolves to
    an installed FastMCP 3.x build would serve requests on the wrong major
    version while the declared dependency still reads as satisfied.
    """

    try:
        declared_major = _declared_fastmcp_major()
        served_major = _leading_major_version(importlib.metadata.version("fastmcp"))
        return (
            declared_major is not None
            and served_major is not None
            and declared_major == served_major
        )
    except Exception:  # noqa: BLE001 - the result is intentionally aggregate-only
        return False


def run_canary() -> dict[str, Any]:
    """Return only aggregate booleans; never return paths, versions, or identities."""

    checks = {
        "entry_points": _entry_points_ready(),
        "engine_binary": _engine_binary_ready(),
        "numeric_kernel": _numeric_kernel_ready(),
        "served_fastmcp_matches_declared": _served_fastmcp_matches_declared(),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "privacySafe": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-os-release-canary")
    parser.add_argument(
        "--json",
        action="store_true",
        required=True,
        help="Emit the bounded JSON release result.",
    )
    parser.parse_args(argv)
    try:
        report = run_canary()
    except Exception:  # noqa: BLE001 - no environment detail crosses this boundary
        report = {
            "status": "failed",
            "checks": {"canary_boundary": False},
            "privacySafe": True,
        }
    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
