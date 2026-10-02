"""Dynamic, name-based import edges: string literals that select a module.

GraphOS has more than one plugin/registration mechanism that chooses a
``graph_os`` submodule by a string at runtime instead of a static ``import``
statement -- for example ``graph_os.gateway.registry``'s widget loader
(``importlib.import_module(module_path)`` where ``module_path`` comes from a
``dict[str, str]`` of absolute dotted names) and ``graph_os.deployment``'s
lazy-submodule facade (``import_module(".doctor", __name__)``, a relative
dotted name). The reachability report (unlike the narrower, unchanged orphan
check in :mod:`scripts.wiring.orphans`) must follow these or it would
misreport every dynamically loaded module as unreachable.

Any string literal anywhere in a module's body that resolves to another
module in the discovered universe -- absolute (``"graph_os.a.b"``) or
relative (``".a.b"``, resolved like a relative ``import``) -- counts as one
dynamic edge from that module to the target.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .static_imports import module_base, parse

_RELATIVE = re.compile(r"^\.+[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _string_literals(tree: ast.Module) -> set[str]:
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _resolve_relative(value: str, base: list[str]) -> str:
    """Resolve a relative dotted literal the way ``from . import x`` does,
    against ``base`` -- the package the owning module resolves imports
    against (see ``static_imports.module_base``)."""
    level = len(value) - len(value.lstrip("."))
    rest = value[level:]
    prefix = base[: len(base) - (level - 1)] if level else base
    return ".".join([*prefix, rest] if rest else prefix)


def dynamic_targets(name: str, path: Path, modules: dict[str, Path]) -> set[str]:
    """The package modules a string literal in ``name`` names, by value."""
    tree = parse(path)
    base = module_base(name, path)
    found: set[str] = set()
    for value in _string_literals(tree):
        if value in modules:
            found.add(value)
        elif value.startswith(".") and _RELATIVE.match(value):
            resolved = _resolve_relative(value, base)
            if resolved in modules:
                found.add(resolved)
    found.discard(name)
    return found
