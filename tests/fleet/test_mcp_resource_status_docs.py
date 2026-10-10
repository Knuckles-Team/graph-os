"""Status docs must describe exactly the vocabulary the reconciliation gate returns."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from graph_os.fleet import mcp_resource_reconciliation as gate

DOCS = Path(__file__).resolve().parents[2] / "docs"
UNRECONCILED = "reingestion-unreconciled"


def _gate_unreconciled_codes() -> set[str]:
    return {code for code in get_args(gate.GateStatus) if code != "reconciled"}


@pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R004")
def test_gate_has_single_unreconciled_code() -> None:
    assert _gate_unreconciled_codes() == {UNRECONCILED}


@pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R004")
@pytest.mark.parametrize("doc", ["status.md", "fleet.md"])
def test_docs_cite_gate_unreconciled_code(doc: str) -> None:
    text = (DOCS / doc).read_text(encoding="utf-8")
    assert f"`{UNRECONCILED}`" in text


@pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R004")
def test_status_row_states_no_publication_claim() -> None:
    rows = [
        line
        for line in (DOCS / "status.md").read_text(encoding="utf-8").splitlines()
        if "four-family MCP resource/template reconciliation" in line
    ]
    assert len(rows) == 1
    assert f"`{UNRECONCILED}`" in rows[0]
    assert "does not claim publication" in rows[0]


@pytest.mark.spec("GRAPHOS-MCP-RESOURCES-R004")
def test_docs_do_not_invent_unreconciled_codes() -> None:
    for doc in ("status.md", "fleet.md"):
        text = (DOCS / doc).read_text(encoding="utf-8")
        for token in text.replace("`", " ").split():
            if token.endswith("-unreconciled"):
                assert token.strip(".,;:") in _gate_unreconciled_codes()
