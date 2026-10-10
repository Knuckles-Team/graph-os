"""GRAPHOS-FLEET-R006.1/.2: the harvest call-site inventory, and the first
removal.

``graph_os/fleet/multiplexer.py`` still carries the skill/prompt
body-harvest path (``_harvest_resource_bodies`` and its helpers) that
``GRAPHOS-FLEET-R006.2``-``.5`` remove once the resident local-skill
catalog (``graph_os/fleet/local_skill_catalog.py``) serves the same
capability for an admitted child. This is a static, source-level census:
it enumerates every ``_harvest``-named entry point defined in that module
and every direct call site of ``_harvest_resource_bodies`` inside the
probe methods that invoke it, and pins the counts as the baseline the
later children must shrink. ``GRAPHOS-FLEET-R006.2`` moved ``_probe_skills``'
and ``_probe_prompts``' call sites behind the new
``_resolve_via_local_catalog_or_harvest`` cutover helper -- a non-admitted
child still reaches ``_harvest_resource_bodies`` through it, but the call
site no longer lives textually inside either probe method, so those two
entries drop out of the pinned baseline below. A failure here means a
harvest-named symbol or call site was added or removed without updating
the pinned baseline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import graph_os

_MULTIPLEXER_RELATIVE_PATH = Path("fleet") / "multiplexer.py"

# GRAPHOS-FLEET-R006.1 baseline: every ``_harvest``-named entry point
# (function or method) defined in graph_os/fleet/multiplexer.py today.
# GRAPHOS-FLEET-R006.3 deletes _harvest_resource_bodies, _harvest_deadline
# and _harvest_error_reason; GRAPHOS-FLEET-R006.4 renames
# _harvest_probe_results off this list.
_BASELINE_HARVEST_ENTRY_POINTS = frozenset(
    {
        "_harvest_deadline",
        "_harvest_error_reason",
        "_harvest_resource_bodies",
        "_harvest_probe_results",
    }
)

# GRAPHOS-FLEET-R006.1/.2 baseline: call sites of _harvest_resource_bodies,
# by the enclosing probe method. GRAPHOS-FLEET-R006.2 dropped the
# _probe_skills and _probe_prompts call sites for an admitted child (the
# fleet-harvest fallback for a non-admitted child now runs through
# _resolve_via_local_catalog_or_harvest instead, which is not one of the
# three probe methods this census tracks), and GRAPHOS-FLEET-R006.3 drops
# the remaining _probe_protocol_families sites.
_BASELINE_CALL_SITES_BY_METHOD = {
    "_probe_protocol_families": 2,
}
_BASELINE_TOTAL_CALL_SITES = sum(_BASELINE_CALL_SITES_BY_METHOD.values())


def _multiplexer_source_path() -> Path:
    package_root = Path(graph_os.__file__).resolve().parent
    return package_root / _MULTIPLEXER_RELATIVE_PATH


def _multiplexer_tree() -> ast.Module:
    path = _multiplexer_source_path()
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _harvest_named_entry_points(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("_harvest")
    }


def _call_target_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _harvest_resource_bodies_call_sites_by_method(
    tree: ast.Module,
) -> dict[str, int]:
    """Count calls to ``_harvest_resource_bodies`` inside each of the three
    probe methods that invoke it, keyed by the enclosing method's name."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name not in _BASELINE_CALL_SITES_BY_METHOD:
            continue
        calls = sum(
            1
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and _call_target_name(call) == "_harvest_resource_bodies"
        )
        if calls:
            counts[node.name] = calls
    return counts


@pytest.mark.spec("GRAPHOS-FLEET-R006.1")
def test_harvest_named_entry_points_match_pinned_baseline() -> None:
    found = _harvest_named_entry_points(_multiplexer_tree())
    assert found == _BASELINE_HARVEST_ENTRY_POINTS, (
        "a harvest-named entry point was added or removed in "
        "graph_os/fleet/multiplexer.py without updating the "
        "GRAPHOS-FLEET-R006.1 baseline: "
        f"found={sorted(found)} baseline={sorted(_BASELINE_HARVEST_ENTRY_POINTS)}"
    )


@pytest.mark.spec("GRAPHOS-FLEET-R006.1")
def test_harvest_resource_bodies_call_sites_match_pinned_baseline() -> None:
    found = _harvest_resource_bodies_call_sites_by_method(_multiplexer_tree())
    assert found == _BASELINE_CALL_SITES_BY_METHOD, (
        "a _harvest_resource_bodies call site was added or removed in "
        "graph_os/fleet/multiplexer.py without updating the "
        f"GRAPHOS-FLEET-R006.1 baseline: found={found} "
        f"baseline={_BASELINE_CALL_SITES_BY_METHOD}"
    )
    assert sum(found.values()) == _BASELINE_TOTAL_CALL_SITES
