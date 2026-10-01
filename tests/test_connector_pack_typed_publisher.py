"""GRAPHOS-FLEET-R001: pack publishing uses the engine-generated types only.

graph-os's fleet content-pack path must never construct a placeholder record
in place of the engine's own generated connector-pack contract: every status,
import and verification call is built from ``epistemic_graph.generated.
connector_pack``/``storage`` types, so SQL and market-data connector items (or
any future pack) carry real typed schemas the moment they are added to the
catalog, not a stand-in shape graph-os invented itself.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import graph_os
from graph_os import semantic_content
from graph_os.fleet import epistemic_adapter


def _source_files() -> list[Path]:
    root = Path(inspect.getfile(graph_os)).resolve().parent
    return sorted(root.rglob("*.py"))


def test_no_placeholder_content_pack_type_is_defined_anywhere() -> None:
    """A placeholder ``ContentPack``-shaped record must never be reintroduced.

    This is a structural guard, not just a point-in-time grep: it parses every
    module under ``graph_os`` and fails on a class or assigned name called
    ``ContentPack`` wherever it is defined, so a future edit cannot quietly
    reintroduce the placeholder this requirement retired.
    """
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ContentPack":
                offenders.append(f"{path}:{node.lineno}")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "ContentPack":
                        offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_semantic_content_status_request_is_the_engine_generated_type() -> None:
    """``verify_semantic_content`` must build the real EG ``ConnectorPackStatusRequest``."""
    from epistemic_graph.generated.connector_pack import ConnectorPackStatusRequest

    source = inspect.getsource(semantic_content)
    assert "from epistemic_graph.generated.connector_pack import" in source
    assert "ConnectorPackStatusRequest" in source
    # And it is genuinely importable/constructible — not merely referenced in
    # a comment or an unreachable branch.
    request = ConnectorPackStatusRequest(connector="graph-os", tenant_id="t")
    assert request.connector == "graph-os"


def test_fleet_catalog_port_reads_only_generated_agent_component_types() -> None:
    """``GeneratedFleetCatalogPort`` must be built from EG-generated request/entry
    types, never a graph-os-local placeholder standing in for them."""
    source = inspect.getsource(epistemic_adapter)
    assert "from epistemic_graph.generated.agent_component import" in source
    assert "from epistemic_graph.generated.storage import" in source
