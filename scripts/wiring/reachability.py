"""Reachability from the package's declared roots.

Unlike :mod:`scripts.wiring.orphans` (isolated modules: zero fan-in AND zero
fan-out), this asks a stricter question: starting from the roots a real
process actually uses to start GraphOS -- the console scripts and entry
points in ``pyproject.toml`` -- which ``graph_os`` modules does walking every
import edge (static, see ``static_imports``, and dynamic/name-based, see
``dynamic_imports``) ever reach? A cluster of modules that only import each
other, reachable from no root, has nonzero fan-in/fan-out and passes the
orphan check, but is still unserved code.

Importing ``a.b.c`` also initializes ``a`` and ``a.b`` (real Python import
semantics); edges are expanded to every discovered ancestor package of a
target so an ``__init__.py`` is reachable whenever any of its descendants is,
without needing a separate exemption from this report.
"""

from __future__ import annotations

from pathlib import Path

from .discovery import check_roots_exist, declared_roots, discovered_modules
from .dynamic_imports import dynamic_targets
from .static_imports import imports


def _expand_ancestors(targets: set[str], modules: dict[str, Path]) -> set[str]:
    expanded = set(targets)
    for target in targets:
        parts = target.split(".")
        expanded.update(
            ".".join(parts[:n])
            for n in range(1, len(parts))
            if ".".join(parts[:n]) in modules
        )
    return expanded


def _build_edges(modules: dict[str, Path]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for name, path in modules.items():
        direct = imports(name, path, modules) | dynamic_targets(name, path, modules)
        edges[name] = _expand_ancestors(direct, modules)
    return edges


def _bfs_reachable(roots: set[str], edges: dict[str, set[str]]) -> set[str]:
    visited = set(roots)
    frontier = list(roots)
    while frontier:
        current = frontier.pop()
        for neighbor in edges.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)
    return visited


def unreachable(root: Path) -> list[str]:
    """Modules no declared root reaches, by any import edge."""
    modules = discovered_modules(root)
    roots = declared_roots(root) & modules.keys()
    check_roots_exist(declared_roots(root), modules)
    edges = _build_edges(modules)
    reached = _bfs_reachable(roots, edges)
    return sorted(name for name in modules if name not in reached)
