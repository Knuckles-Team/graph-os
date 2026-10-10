"""GRAPHOS-OPS-R032.1: identity dependency inversion via ports.

Static dependency-graph check: graph_os.webui_host, graph_os.webui_co_service,
and graph_os.mcp_server must import only graph_os.identity.ports (the
Protocol/DTO boundary) and never the concrete identity runtime
(graph_os.identity.engine or any other graph_os.identity submodule), per
specs/hosted-api-operations/requirements.md GRAPHOS-OPS-R032.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

GRAPH_OS_ROOT = Path(__file__).resolve().parents[2] / "graph_os"
CHECKED_ROOTS = (
    GRAPH_OS_ROOT / "webui_host",
    GRAPH_OS_ROOT / "mcp_server",
)
ALLOWED_IDENTITY_MODULE = "graph_os.identity.ports"


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "test" not in p.parts]


def _identity_import_violations(source_file: Path) -> list[str]:
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("graph_os.identity") and (
                    alias.name != ALLOWED_IDENTITY_MODULE
                ):
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                module.startswith("graph_os.identity")
                and module != ALLOWED_IDENTITY_MODULE
            ):
                violations.append(module)
    return violations


@pytest.mark.spec("GRAPHOS-OPS-R032.1")
def test_serving_modules_depend_only_on_identity_ports() -> None:
    offenders: dict[str, list[str]] = {}
    for root in CHECKED_ROOTS:
        for source_file in _python_files(root):
            violations = _identity_import_violations(source_file)
            if violations:
                offenders[str(source_file)] = violations
    assert not offenders, (
        "serving modules must depend only on graph_os.identity.ports, not the "
        f"concrete identity runtime: {offenders}"
    )
