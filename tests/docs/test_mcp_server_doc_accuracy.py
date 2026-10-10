"""GRAPHOS-OPS-R030: accurate MCP server and API documentation.

GraphOS keeps its MCP server documentation accurate to the live six-verb
MCP surface, replacing any outdated tool-count or endpoint description.
This test parses the resident-tool-surface description published in
``docs/mcp-server.md`` and confirms its stated verb/tool counts and names
match the live registries, so a future registry change that isn't reflected
in the doc fails CI instead of silently drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from graph_os.api.mcp.registration import FLEET_OPERATIONS, RESIDENT_NAMES
from graph_os.api.mcp.verbs import VERBS

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "mcp-server.md"


@pytest.mark.spec("GRAPHOS-OPS-R030")
def test_doc_states_resident_tool_total_matches_live_registry() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    match = re.search(r"exactly (\w+) resident tools", text)
    assert match is not None, (
        "docs/mcp-server.md must state the resident tool total so it can be "
        "checked against the live registry"
    )
    number_words = {"ten": 10}
    stated_count = number_words.get(match.group(1).lower())
    assert stated_count is not None, f"unrecognized count word: {match.group(1)!r}"
    assert stated_count == len(RESIDENT_NAMES)


@pytest.mark.spec("GRAPHOS-OPS-R030")
def test_doc_lists_every_live_verb_and_fleet_tool_name() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in (*VERBS, *FLEET_OPERATIONS):
        assert f"`{name}`" in text, (
            f"docs/mcp-server.md does not mention live resident tool {name!r}"
        )
