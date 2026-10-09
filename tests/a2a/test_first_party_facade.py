"""GRAPHOS-A2A-R008: the A2A facade is first-party, not a vendored wrapper.

graph-os implements the A2A protocol facade itself as source under
``graph_os/a2a`` over the public graph-engine and agent orchestration ports,
rather than depending on or wrapping an external vendored A2A implementation
(e.g. an ``a2a-sdk``-style package). This is a static, source-level
dependency check: the declared distribution requirements never pull in a
third-party A2A package, and the package's own source under ``graph_os/a2a``
never imports one.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

import graph_os
import graph_os.a2a
from graph_os.a2a.application import create_a2a_application


def _package_root() -> Path:
    return Path(graph_os.__file__).resolve().parent


def _repo_root() -> Path:
    return _package_root().parent


def _a2a_source_files() -> list[Path]:
    return sorted(Path(graph_os.a2a.__file__).resolve().parent.rglob("*.py"))


def _declared_dependencies() -> list[str]:
    pyproject = _repo_root() / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def _imported_top_level_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.spec("GRAPHOS-A2A-R008")
def test_no_declared_dependency_is_a_vendored_a2a_implementation() -> None:
    offenders = [
        dependency
        for dependency in _declared_dependencies()
        if "a2a" in dependency.lower()
    ]
    assert not offenders, (
        "graph-os declares a dependency on what looks like a vendored A2A "
        f"implementation instead of implementing the facade first-party: {offenders}"
    )


@pytest.mark.spec("GRAPHOS-A2A-R008")
def test_a2a_source_never_imports_an_external_a2a_package() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _a2a_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = {
            module
            for module in _imported_top_level_modules(tree)
            if "a2a" in module.lower() and module != "graph_os"
        }
        if hits:
            offenders[path.relative_to(_package_root()).as_posix()] = hits
    assert not offenders, (
        "graph_os/a2a source imports an external A2A-named package, "
        f"suggesting a vendored wrapper rather than a first-party facade: {offenders}"
    )


@pytest.mark.spec("GRAPHOS-A2A-R008")
def test_a2a_facade_entry_point_is_implemented_in_this_repository() -> None:
    module = create_a2a_application.__module__
    assert module.startswith("graph_os.a2a"), (
        "the A2A facade entry point must be implemented under graph_os.a2a, "
        f"found: {module}"
    )
    source_file = Path(create_a2a_application.__globals__["__file__"]).resolve()
    assert _repo_root() in source_file.parents, (
        f"A2A facade entry point source {source_file} is not part of this repository"
    )
