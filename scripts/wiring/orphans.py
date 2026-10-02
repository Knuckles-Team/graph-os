"""The orphan-module check: isolated modules (zero fan-in AND zero fan-out).

Unchanged from the gate's original, narrower structural definition -- see
``scripts/wiring/static_imports.py``. This is the check the ``orphans``
command and the ``check-orphan-modules`` pre-commit hook run; it continues to
block as it always has.
"""

from __future__ import annotations

from pathlib import Path

from .discovery import check_roots_exist, declared_roots, discovered_modules
from .static_imports import imports


def orphans(root: Path) -> list[str]:
    """Modules with zero fan-in and zero fan-out (isolated)."""
    modules = discovered_modules(root)
    roots = declared_roots(root)
    check_roots_exist(roots, modules)
    edges = {name: imports(name, path, modules) for name, path in modules.items()}
    fan_in: set[str] = set().union(*edges.values()) if edges else set()
    candidates = (
        name
        for name, path in modules.items()
        if name not in roots and path.name != "__init__.py"
    )
    return sorted(name for name in candidates if not edges[name] and name not in fan_in)
