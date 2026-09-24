"""Every control-lease kind graph-os-hosted code issues is classified by the
identity that signs it, and the documented EG allowlist for graph-os is exactly
the process-identity kinds (operator ruling 2026-09-24)."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path

import agent_utilities

import graph_os
from graph_os.lease_kinds import (
    CALLER_IDENTITY_LEASE_KINDS,
    PROCESS_IDENTITY_LEASE_KINDS,
)

DEPLOYMENT_DOC = Path(__file__).resolve().parents[1] / "docs" / "deployment.md"
ISSUERS = {"issue", "issue_record"}
ID_KEYWORDS = {"lease_id", "record_id"}
SCANNED = (Path(graph_os.__file__).parent, Path(agent_utilities.__file__).parent)


def _kind_constants(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value.value, str)
    }


def _issue_calls(tree: ast.Module) -> Iterator[tuple[ast.Call, set[str]]]:
    """Each lease issue call with the parameters of its enclosing function."""
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        params = {arg.arg for arg in func.args.args + func.args.kwonlyargs}
        for call in ast.walk(func):
            if isinstance(call, ast.Call) and _is_issue(call):
                yield call, params


def _is_issue(call: ast.Call) -> bool:
    name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
    keywords = {kw.arg for kw in call.keywords}
    return name in ISSUERS and "kind" in keywords and bool(keywords & ID_KEYWORDS)


def _kinds(call: ast.Call, params: set[str], constants: dict[str, str]) -> set[str]:
    [kind] = [kw.value for kw in call.keywords if kw.arg == "kind"]
    if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
        return {kind.value}
    if isinstance(kind, ast.Name) and kind.id in constants:
        return {constants[kind.id]}
    if isinstance(kind, ast.Name) and kind.id in params:
        # A helper taking the kind: every *_KIND constant of its module.
        return {v for k, v in constants.items() if k.endswith("_KIND")}
    raise AssertionError(f"unresolvable lease kind at line {call.lineno}")


def issued_kinds() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for root in SCANNED:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            constants = _kind_constants(tree)
            for call, params in _issue_calls(tree):
                for kind in _kinds(call, params, constants):
                    found.setdefault(kind, set()).add(f"{path.name}:{call.lineno}")
    return found


def _documented_allowlist() -> list[str]:
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    [raw] = re.findall(
        r"EPISTEMIC_GRAPH_CONTROL_LEASE_KIND_POLICY_JSON='([^']+)'", text
    )
    [(principal, kinds)] = json.loads(raw).items()
    assert principal == "<graph-os agent_id>"
    return kinds


def test_every_issued_lease_kind_is_classified_by_its_signing_identity() -> None:
    found = issued_kinds()
    assert found, "the scan must find the known issue sites"
    classified = PROCESS_IDENTITY_LEASE_KINDS | CALLER_IDENTITY_LEASE_KINDS
    unclassified = {
        kind: sites for kind, sites in found.items() if kind not in classified
    }
    assert not unclassified, f"classify in graph_os/lease_kinds.py: {unclassified}"
    assert set(found) == classified, "a classified kind is no longer issued"
    assert not PROCESS_IDENTITY_LEASE_KINDS & CALLER_IDENTITY_LEASE_KINDS


def test_the_documented_graph_os_allowlist_is_exactly_its_process_kinds() -> None:
    assert sorted(_documented_allowlist()) == sorted(PROCESS_IDENTITY_LEASE_KINDS)
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    for kind in CALLER_IDENTITY_LEASE_KINDS:
        assert f"`{kind}`" in text, f"{kind} is not documented"


def test_the_scan_catches_an_unclassified_kind() -> None:
    tree = ast.parse(
        "ROGUE_KIND = 'rbac.elevation'\n"
        "def f(client):\n"
        "    client.control_leases.issue(lease_id='x', kind=ROGUE_KIND)\n"
    )
    [(call, params)] = list(_issue_calls(tree))
    assert _kinds(call, params, _kind_constants(tree)) == {"rbac.elevation"}
