"""GRAPHOS-IDENTITY-R021.1: identity wiring depends only on identity ports.

GraphOS's MCP server, web host (gateway), and web co-service (webui_host)
packages must depend only on ``graph_os.identity.ports`` (the Protocol
definitions + DTOs) for identity behavior, never on a concrete issuer,
session, or authority implementation module. The concrete implementations
are constructed once in the serving composition root and injected; these
packages receiving them must not re-import the concrete modules themselves,
or a second, uncoordinated implementation becomes possible and the
composition root stops being the single source of truth for identity
wiring.

This is a regression guard: at the time this test was written none of the
three packages imported ``graph_os.identity`` at all (identity objects are
injected, never imported concretely), so the test is expected to pass
trivially today and to fail loudly the moment a future edit reaches past
the ports module.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

import graph_os.gateway as gateway_pkg
import graph_os.mcp_server as mcp_server_pkg
import graph_os.webui_host as webui_host_pkg

#: Only the ports/DTO module may be imported by a dependent package; every
#: other ``graph_os.identity`` submodule holds a concrete implementation.
_ALLOWED_IDENTITY_MODULE = "graph_os.identity.ports"

_CHECKED_PACKAGES = {
    "graph_os.mcp_server": mcp_server_pkg,
    "graph_os.gateway": gateway_pkg,
    "graph_os.webui_host": webui_host_pkg,
}


def _package_source_files(package) -> list[Path]:
    root = Path(package.__path__[0])
    files = [root / "__init__.py"] if (root / "__init__.py").exists() else []
    for module_info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        candidate = root
        for part in module_info.name[len(package.__name__) + 1 :].split("."):
            candidate = candidate / part
        py_file = candidate.with_suffix(".py")
        if py_file.exists():
            files.append(py_file)
    return files


def _is_identity_module(module: str) -> bool:
    return module == "graph_os.identity" or module.startswith("graph_os.identity.")


def _forbidden_plain_imports(node: ast.Import) -> set[str]:
    return {
        alias.name
        for alias in node.names
        if _is_identity_module(alias.name) and alias.name != _ALLOWED_IDENTITY_MODULE
    }


def _forbidden_from_imports(node: ast.ImportFrom) -> set[str]:
    module = node.module or ""
    if module == "graph_os.identity":
        # `from graph_os.identity import <concrete symbol>` — the package's
        # own __init__ re-exports concrete engine types, so importing the
        # bare package is itself a concrete-impl dependency, not a
        # ports-only one.
        return {f"{module}.{alias.name}" for alias in node.names}
    if _is_identity_module(module) and module != _ALLOWED_IDENTITY_MODULE:
        return {module}
    return set()


def _forbidden_identity_imports(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden |= _forbidden_plain_imports(node)
        elif isinstance(node, ast.ImportFrom):
            forbidden |= _forbidden_from_imports(node)
    return forbidden


@pytest.mark.parametrize("package_name", sorted(_CHECKED_PACKAGES))
def test_package_depends_only_on_identity_ports(package_name: str) -> None:
    package = _CHECKED_PACKAGES[package_name]
    violations: dict[str, set[str]] = {}
    for source_file in _package_source_files(package):
        found = _forbidden_identity_imports(source_file)
        if found:
            violations[str(source_file)] = found
    assert not violations, (
        f"{package_name} must depend only on {_ALLOWED_IDENTITY_MODULE} for "
        f"identity behavior (concrete implementations are injected from the "
        f"composition root), but found: {violations}"
    )
