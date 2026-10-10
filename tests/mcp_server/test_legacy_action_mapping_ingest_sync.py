"""R005.2.3.1: legacy ``sync`` resolves to a real ingest registry operation."""

from __future__ import annotations

import pytest

from graph_os.api.ops import ingest
from graph_os.mcp_server.legacy_action_mapping import APPROVED_ACTION_MAPPING


@pytest.mark.spec("GRAPHOS-HOST-R005.2.3.1")
def test_sync_maps_to_registered_ingest_operation() -> None:
    assert APPROVED_ACTION_MAPPING["sync"] == "ingest.sources.sync"
    assert APPROVED_ACTION_MAPPING["sync"] in {op.id for op in ingest.operations()}
