"""Live wiring and dependency-boundary checks for the G1 extraction."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import graph_os.control_plane as control_plane


def _module_names() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.walk_packages(
            control_plane.__path__, f"{control_plane.__name__}."
        )
    )


@pytest.mark.parametrize("module_name", _module_names())
def test_every_extracted_module_is_live_importable(module_name: str) -> None:
    """The package transplant must be wired, not just present on disk."""
    assert importlib.import_module(module_name).__name__ == module_name


@pytest.mark.parametrize(
    "package_name",
    [
        "agents",
        "connectors",
        "economics",
        "foundation",
        "migrations",
        "policy",
        "projection",
        "retrieval",
        "runs",
        "sources",
        "webui",
        "workflows",
    ],
)
def test_subpackage_exports_are_reachable_from_graph_os(package_name: str) -> None:
    """Public subpackage entrypoints resolve through the new owner."""
    module = importlib.import_module(f"{control_plane.__name__}.{package_name}")
    for symbol in module.__all__:
        assert hasattr(module, symbol), f"{module.__name__}.{symbol} is not wired"


def test_extraction_has_only_the_approved_agent_utilities_dependency() -> None:
    """G1 control-plane code must not reach back into AU internals."""
    package_root = Path(control_plane.__file__).parent
    imported_agent_utilities_modules: set[str] = set()

    for source_path in package_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_agent_utilities_modules.update(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("agent_utilities")
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("agent_utilities"):
                    imported_agent_utilities_modules.add(node.module)

    assert imported_agent_utilities_modules == {
        "agent_utilities.protocols.epistemic_operations"
    }
