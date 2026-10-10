"""GRAPHOS-OPS-R029: single resident-tool server cutover.

GraphOS's server factory registers only the six resident MCP verbs plus
``find_tools``, ``load_tools``, ``unload_tools``, and ``multiplexer_status``,
mounts the generated ``/api/v1`` sub-application behind an explicit
protocol-route table, and removes the retired granular tool registrars,
hand-written action-tool routes, and legacy fleet routes.

This test asserts against the static registries the server factory actually
builds the live tool/route surface from (``RESIDENT_NAMES`` and
``PROTOCOL_ROUTES``), rather than booting a full network server, so it stays
fast and hermetic while still failing the moment either registry drifts from
the documented resident set.
"""

from __future__ import annotations

import pytest

from graph_os.api.http.protocol_routes import PROTOCOL_ROUTES
from graph_os.api.mcp.registration import FLEET_OPERATIONS, RESIDENT_NAMES
from graph_os.api.mcp.verbs import VERBS

EXPECTED_VERBS = ("find", "ask", "why", "write", "act", "manage")
EXPECTED_FLEET_TOOLS = (
    "find_tools",
    "load_tools",
    "unload_tools",
    "multiplexer_status",
)


@pytest.mark.spec("GRAPHOS-OPS-R029")
def test_resident_tool_set_is_exactly_six_verbs_plus_four_fleet_tools() -> None:
    assert tuple(VERBS) == EXPECTED_VERBS
    assert tuple(FLEET_OPERATIONS) == EXPECTED_FLEET_TOOLS
    assert set(RESIDENT_NAMES) == set(EXPECTED_VERBS) | set(EXPECTED_FLEET_TOOLS)
    assert len(RESIDENT_NAMES) == 10
    # No duplicate/overlapping registration between verbs and fleet tools.
    assert len(set(EXPECTED_VERBS) & set(EXPECTED_FLEET_TOOLS)) == 0


@pytest.mark.spec("GRAPHOS-OPS-R029")
def test_retired_granular_tool_registrars_are_absent() -> None:
    import importlib

    for retired_module in (
        "graph_os.api.ops.action_tools",
        "graph_os.api.mcp.legacy_routes",
        "graph_os.api.mcp.granular_registration",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(retired_module)


@pytest.mark.spec("GRAPHOS-OPS-R029")
def test_protocol_route_table_is_explicit_and_non_empty() -> None:
    paths = [route.path for route in PROTOCOL_ROUTES]
    assert len(paths) == len(set(paths)), "protocol route table has duplicate paths"
    assert "/mcp" in paths
    assert "/health" in paths
    assert "/health/ready" in paths
    for route in PROTOCOL_ROUTES:
        assert route.path
        assert route.owner
        assert route.reason
