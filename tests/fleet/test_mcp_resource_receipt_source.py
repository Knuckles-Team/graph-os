"""GRAPHOS-MCP-RESOURCES-R002: only the reconciliation module constructs receipts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "graph_os"
_OWNER = _PACKAGE_ROOT / "fleet" / "mcp_resource_reconciliation.py"
_NAME = "ReconciliationReceipt"


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _constructs_receipt(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call) and _callee_name(node) == _NAME
        for node in ast.walk(tree)
    )


@pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R002")
def test_no_graph_os_module_constructs_receipt_outside_gate_module() -> None:
    modules = sorted(_PACKAGE_ROOT.rglob("*.py"))
    assert _OWNER in modules
    offenders = [
        str(p.relative_to(_PACKAGE_ROOT))
        for p in modules
        if p != _OWNER and _constructs_receipt(p)
    ]
    assert offenders == []
