"""GRAPHOS-HOST-R006: graph_os.fleet is the sole multiplexer implementation.

A package-layout test that fails closed if a *second* multiplexer class is
ever defined outside ``graph_os/fleet/`` -- the shape the requirement names
("the duplicate multiplexer no longer exists outside graph_os/fleet/").
"""

from __future__ import annotations

import pytest

import ast
from pathlib import Path

GRAPH_OS_ROOT = Path(__file__).resolve().parents[2] / "graph_os"
FLEET_ROOT = GRAPH_OS_ROOT / "fleet"


def _multiplexer_class_defs(source_file: Path) -> list[str]:
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and "multiplexer" in node.name.lower()
    ]


@pytest.mark.spec('GRAPHOS-HOST-R006')
def test_fleet_defines_the_multiplexer_implementation() -> None:
    found = {
        name: path
        for path in FLEET_ROOT.rglob("*.py")
        for name in _multiplexer_class_defs(path)
    }
    assert "MCPMultiplexer" in found


@pytest.mark.spec('GRAPHOS-HOST-R006')
def test_no_multiplexer_class_is_defined_outside_graph_os_fleet() -> None:
    offenders = {
        (path, name)
        for path in GRAPH_OS_ROOT.rglob("*.py")
        if FLEET_ROOT not in path.parents
        for name in _multiplexer_class_defs(path)
    }
    assert offenders == set(), (
        f"duplicate multiplexer implementation(s) found outside graph_os/fleet/: {offenders}"
    )
