"""GRAPHOS-IDENTITY-R021: identity wiring depends only on identity ports.

The served-runtime packages (web UI host, web co-service, MCP server, and the
fleet multiplexer they compose into) must reach identity behavior only
through ``graph_os.identity.ports`` (and the port registry in
``graph_os.identity.engine_ports``) -- never a concrete identity module such
as ``engine``, ``issuer``, ``admission``, ``broker``, ``mfa``,
``admin_console``, ``browser``, or ``bootstrap``. Those concrete
implementations are constructed once, in the serving composition root, and
injected as the port protocol -- not imported directly by the packages that
consume identity behavior.

This is an import-graph test: it walks the source of each served package
with ``ast`` (no execution, so it is immune to optional runtime
dependencies) and asserts the module names actually imported stay within the
allowed port surface, and that ``graph_os.identity`` itself never imports
back from a served package (no import cycle).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_OS = REPO_ROOT / "graph_os"

#: Packages that serve requests and must depend only on identity *ports*,
#: never a concrete identity implementation module.
SERVED_PACKAGES = ("webui_host", "mcp_server", "fleet")

#: The identity submodules that are ports/DTOs, not concrete authorities.
#: Importing these is the sanctioned dependency; everything else under
#: ``graph_os.identity`` is a concrete implementation detail of the
#: composition root.
ALLOWED_IDENTITY_SUBMODULES = {"ports", "engine_ports"}

#: Concrete identity modules a served package may never import directly.
CONCRETE_IDENTITY_SUBMODULES = {
    "engine",
    "issuer",
    "admission",
    "broker",
    "mfa",
    "admin_console",
    "browser",
    "bootstrap",
}


def _iter_python_files(package_dir: Path) -> list[Path]:
    return sorted(package_dir.rglob("*.py"))


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _identity_submodule(module_name: str) -> str | None:
    prefix = "graph_os.identity"
    if module_name == prefix:
        return ""
    if not module_name.startswith(prefix + "."):
        return None
    return module_name[len(prefix) + 1 :].split(".")[0]


@pytest.mark.spec("GRAPHOS-IDENTITY-R021")
@pytest.mark.parametrize("package_name", SERVED_PACKAGES)
def test_served_package_depends_only_on_identity_ports(package_name: str) -> None:
    package_dir = GRAPH_OS / package_name
    assert package_dir.is_dir(), f"expected {package_dir} to exist"
    violations: list[str] = []
    for path in _iter_python_files(package_dir):
        for module_name in _imported_module_names(path):
            submodule = _identity_submodule(module_name)
            if submodule is None:
                continue
            if submodule in CONCRETE_IDENTITY_SUBMODULES:
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} imports {module_name!r}"
                )
            elif submodule and submodule not in ALLOWED_IDENTITY_SUBMODULES:
                # An identity submodule not explicitly classified: fail
                # closed rather than silently allow a new concrete module.
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} imports unclassified "
                    f"identity submodule {module_name!r}"
                )
    assert not violations, (
        "served packages may depend only on graph_os.identity.ports / "
        "engine_ports, not a concrete identity module:\n" + "\n".join(violations)
    )


@pytest.mark.spec("GRAPHOS-IDENTITY-R021")
def test_identity_package_has_no_import_cycle_with_served_packages() -> None:
    identity_dir = GRAPH_OS / "identity"
    violations: list[str] = []
    for path in _iter_python_files(identity_dir):
        for module_name in _imported_module_names(path):
            if any(
                module_name == f"graph_os.{package_name}"
                or module_name.startswith(f"graph_os.{package_name}.")
                for package_name in SERVED_PACKAGES
            ):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} imports {module_name!r}"
                )
    assert not violations, (
        "graph_os.identity must not import back from a served package "
        "(import cycle):\n" + "\n".join(violations)
    )
