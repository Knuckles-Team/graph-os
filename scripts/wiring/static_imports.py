"""Static import-edge extraction (absolute and relative ``import`` statements).

This is the exact edge definition the retired ``kiss`` orphan check used:
every module a given module imports, anywhere in its body (including inside a
function), absolute or relative. It is deliberately narrower than
:mod:`scripts.wiring.dynamic_imports` — the orphan gate's structural
definition ("no production fan-in/fan-out") must not change when the
reachability report is added alongside it.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _import_targets(node: ast.AST, base: list[str]) -> set[str]:
    """Every module name one import statement could bind (absolute or relative)."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    prefix = base[: len(base) - (node.level - 1)] if node.level else []
    target = ".".join([*prefix, node.module] if node.module else prefix)
    return {target, *(f"{target}.{alias.name}" for alias in node.names)}


def module_base(name: str, path: Path) -> list[str]:
    """The package a module resolves its OWN relative imports against."""
    return name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imports(name: str, path: Path, modules: dict[str, Path]) -> set[str]:
    """The package modules ``name`` imports anywhere in its body."""
    tree = parse(path)
    base = module_base(name, path)
    found = set().union(*(_import_targets(node, base) for node in ast.walk(tree)))
    return found & modules.keys()
