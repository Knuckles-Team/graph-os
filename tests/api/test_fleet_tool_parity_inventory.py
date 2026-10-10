"""GRAPHOS-OPS-R031.1: fleet meta-tool parity mapping (resident-fleet slice).

Implements the resident-fleet-tool slice of GRAPHOS-OPS-R031 — GraphOS
publishes, in ``docs/mcp-server.md``, the parity mapping from each of its
four resident fleet meta-tools to its destination operation. This test
confirms the published table's rows match the live ``FLEET_OPERATIONS``
registry exactly, so the doc cannot silently drift from the registry it
documents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from graph_os.api.mcp.registration import FLEET_OPERATIONS

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "mcp-server.md"
ROW_PATTERN = re.compile(r"\|\s*`(\w+)`\s*\|\s*`([\w.]+)`\s*\|")


def _parse_published_mapping() -> dict[str, str]:
    text = DOC_PATH.read_text(encoding="utf-8")
    mapping: dict[str, str] = {}
    for tool_name, operation in ROW_PATTERN.findall(text):
        if tool_name in FLEET_OPERATIONS:
            mapping[tool_name] = operation
    return mapping


@pytest.mark.spec("GRAPHOS-OPS-R031.1")
def test_doc_publishes_a_row_for_every_resident_fleet_tool() -> None:
    published = _parse_published_mapping()
    assert set(published) == set(FLEET_OPERATIONS), (
        "docs/mcp-server.md parity table does not cover exactly the live "
        "resident fleet tools"
    )


@pytest.mark.spec("GRAPHOS-OPS-R031.1")
def test_published_mapping_matches_live_fleet_operations_registry() -> None:
    published = _parse_published_mapping()
    assert published == dict(FLEET_OPERATIONS)
