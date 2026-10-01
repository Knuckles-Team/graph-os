#!/usr/bin/env python3
"""GraphOS's Python wiring gate: no orphan module under ``graph_os/``.

This repository previously relied on kiss's own ``[global]
orphan_module_enabled`` static check (``kiss check``). Starting with kiss
0.4.11 that check moved to the coverage-linked ``kiss test`` (``[test]
orphan_detection``), which runs the full test suite and reports orphans only
after every test passes -- a materially different, much heavier operation
than a structural reachability scan. This script restores the original,
cheap, structural semantics directly, independent of the native scanner:

A module under ``graph_os/`` is an orphan when it is isolated: it neither
imports, nor is imported by, any other package module (absolute or relative,
including function-local imports) -- mirroring the retired ``kiss`` rule's
own definition ("no production fan-in/fan-out"). Every ``.py`` file on disk
under ``graph_os/`` is in scope, tracked or not (stricter: a new module is
caught before it is ever staged).
Dynamically dispatched modules (for example ``graph_os.gateway.registry``'s
string-keyed widget loader) are not orphans under this definition as long as
they import something from the package themselves (every widget imports its
shared ``base`` module), exactly as the retired static check treated them.
Declared roots (the top-level ``graph_os`` package, every ``[project.scripts]``
console entry point, and every ``[project.entry-points]`` target) and
``__init__`` modules are never candidates. There is no allowlist: a genuinely
isolated module is wired in, reached by another module, or deleted.

Usage: ``python3 scripts/check_wiring.py orphans [--root REPO]``.
Exit 0 = clean, 1 = findings, 2 = the gate could not establish its universe.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from pathlib import Path

PACKAGE = "graph_os"


class GateError(RuntimeError):
    """The gate could not establish its universe."""


def discovered_modules(root: Path) -> dict[str, Path]:
    """Every ``.py`` file on disk under ``graph_os/``, by dotted module name."""
    base = root / PACKAGE
    modules: dict[str, Path] = {}
    for path in sorted(base.rglob("*.py")) if base.is_dir() else []:
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    if not modules:
        raise GateError(f"no modules found under {PACKAGE}/")
    return modules


def declared_roots(root: Path) -> set[str]:
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read pyproject.toml: {exc}") from exc
    project = document.get("project", {})
    roots = {PACKAGE}
    for target in project.get("scripts", {}).values():
        roots.add(str(target).split(":", 1)[0].strip())
    for group in project.get("entry-points", {}).values():
        roots.update(str(target).split(":", 1)[0].strip() for target in group.values())
    return roots


def _import_targets(node: ast.AST, base: list[str]) -> set[str]:
    """Every module name one import statement could bind (absolute or relative)."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    prefix = base[: len(base) - (node.level - 1)] if node.level else []
    target = ".".join([*prefix, node.module] if node.module else prefix)
    return {target, *(f"{target}.{alias.name}" for alias in node.names)}


def imports(name: str, path: Path, modules: dict[str, Path]) -> set[str]:
    """The package modules ``name`` imports anywhere in its body."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    base = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    found = set().union(*(_import_targets(node, base) for node in ast.walk(tree)))
    return found & modules.keys()


def orphans(root: Path) -> list[str]:
    """Modules with zero fan-in and zero fan-out (isolated)."""
    modules = discovered_modules(root)
    roots = declared_roots(root)
    missing = sorted(r for r in roots if r.startswith(PACKAGE) and r not in modules)
    if missing:
        raise GateError(f"declared roots do not exist: {', '.join(missing)}")
    edges = {name: imports(name, path, modules) for name, path in modules.items()}
    fan_in: set[str] = set().union(*edges.values()) if edges else set()
    candidates = (
        name
        for name, path in modules.items()
        if name not in roots and path.name != "__init__.py"
    )
    return sorted(name for name in candidates if not edges[name] and name not in fan_in)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=["orphans"])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    try:
        findings = orphans(args.root.resolve())
    except (GateError, SyntaxError, OSError, ValueError) as exc:
        print(f"wiring {args.check}: CANNOT RUN: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"  - {finding}")
    if findings:
        print(
            f"wiring {args.check}: FAILED: {len(findings)} module(s) reachable from no "
            "declared root; import it, add it as an entry point, or delete it."
        )
        return 1
    print(f"wiring {args.check}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
