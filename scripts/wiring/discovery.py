"""Shared module universe for GraphOS's wiring gates.

Both the orphan-module check (``scripts/check_wiring.py orphans``) and the
reachability report (``scripts/check_wiring.py unreachable``) start from the
same universe: every ``.py`` file on disk under ``graph_os/`` and the roots
declared in ``pyproject.toml``. Keeping that universe in one place means the
two gates can never silently disagree about what exists or what counts as a
root.
"""

from __future__ import annotations

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


def _pyproject(root: Path) -> dict:
    try:
        return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read pyproject.toml: {exc}") from exc


def declared_roots(root: Path) -> set[str]:
    """The top-level package, every console script, and every entry point."""
    project = _pyproject(root).get("project", {})
    roots = {PACKAGE}
    for target in project.get("scripts", {}).values():
        roots.add(str(target).split(":", 1)[0].strip())
    for group in project.get("entry-points", {}).values():
        roots.update(str(target).split(":", 1)[0].strip() for target in group.values())
    return roots


def check_roots_exist(roots: set[str], modules: dict[str, Path]) -> None:
    """Raise when a declared root has no module on disk (the gate's universe
    is wrong, not that the gate found a real finding)."""
    missing = sorted(r for r in roots if r.startswith(PACKAGE) and r not in modules)
    if missing:
        raise GateError(f"declared roots do not exist: {', '.join(missing)}")
