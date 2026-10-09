"""The admitted EG catalog snapshot backs ``find`` when a live probe cannot
(GRAPHOS-FLEET-R031).

Root cause (gap G30): ``discover_tools`` ranked ONLY the live
``probe_catalog`` answer for each fleet server. Fleet onboarding
(``graph_os/fleet/onboarding.py``) already imports each admitted server's
tools into the EG catalog before any child is ever mounted, but that
snapshot was never read back for discovery — so a server whose live probe
times out (the common case across a real fleet; see
``MCPMultiplexer._unsettled_probe_entry``) contributed no tool rows at all,
only an ``unavailable`` entry. This covers the fix:
:meth:`MCPMultiplexer._catalog_tool_probe_info` reads tool descriptors
straight off the installed ``self._fleet_catalog`` snapshot with NO probe,
and :meth:`MCPMultiplexer.discover_tools` falls back to it only for a
server whose live probe errored — the live probe stays the freshness
refresh, never the only source, and the server still appears in
``unavailable`` so the timeout itself is not hidden.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.fleet.test_multiplexer_dynamic_gateway import CNT, _mux_with_children


def _fake_catalog_server(server_name: str, tools: list[tuple[str, str]]):
    """A minimal stand-in for ``catalog_reader.CatalogServer`` carrying only
    the attributes :meth:`_catalog_tool_probe_info` reads."""
    provides = tuple(
        SimpleNamespace(
            entry=SimpleNamespace(kind="tool", upstream_name=name, summary=desc)
        )
        for name, desc in tools
    )
    return SimpleNamespace(
        component=SimpleNamespace(server_name=server_name),
        provides=provides,
    )


@pytest.mark.spec("GRAPHOS-FLEET-R031")
async def test_discover_tools_ranks_catalog_tools_when_the_live_probe_times_out(
    tmp_path,
):
    mux = _mux_with_children(tmp_path, {CNT: []})
    # The live probe for CNT already "ran" and timed out — exactly the
    # symptom reported in gap G30 (`fleet_unavailable`: "timeout after 10s").
    mux._probe_cache[CNT] = {
        "tools": [],
        "skills": [],
        "error": "timeout after 10s",
    }
    mux._fleet_catalog = SimpleNamespace(
        servers=(
            _fake_catalog_server(
                CNT,
                [
                    ("cm_container_operations", "manage docker containers"),
                    ("cm_image_operations", "manage docker images"),
                ],
            ),
        )
    )
    mux._fleet_catalog_loaded_at = 1_000_000.0

    discovery = await mux.discover_tools("manage docker containers", top_k=5)

    # The timeout is still reported, truthfully — the catalog fallback never
    # hides a real live-probe failure.
    assert discovery["unavailable"][CNT] == "timeout after 10s"

    tool_hits = {r["tool"]: r for r in discovery["results"] if r["kind"] == "tool"}
    assert "cm_container_operations" in tool_hits
    hit = tool_hits["cm_container_operations"]
    assert hit["server"] == CNT
    assert hit["description"] == "manage docker containers"
    # Sourced from the snapshot, not a live answer — reported as stale,
    # never faked as a fresh probe result (CONCEPT:AU-ECO.multiplexer.catalog-probe-ttl-staleness).
    assert hit["stale"] is True


@pytest.mark.spec("GRAPHOS-FLEET-R031")
async def test_catalog_tool_probe_info_is_empty_with_no_installed_snapshot(tmp_path):
    mux = _mux_with_children(tmp_path, {CNT: []})
    assert mux._fleet_catalog is None
    assert mux._catalog_tool_probe_info() == {}


@pytest.mark.spec("GRAPHOS-FLEET-R031")
async def test_live_probe_tools_are_not_shadowed_by_the_catalog_fallback(tmp_path):
    """A server whose live probe DID answer ranks its live tools — the
    catalog snapshot is a fallback for a failed probe, never a substitute
    for a successful one."""
    mux = _mux_with_children(tmp_path, {CNT: []})
    mux._probe_cache[CNT] = {
        "tools": [
            {
                "name": "cm_container_operations",
                "description": "LIVE manage docker containers",
            }
        ],
        "skills": [],
        "error": None,
    }
    mux._fleet_catalog = SimpleNamespace(
        servers=(
            _fake_catalog_server(
                CNT,
                [("cm_container_operations", "STALE manage docker containers")],
            ),
        )
    )
    mux._fleet_catalog_loaded_at = 1_000_000.0

    discovery = await mux.discover_tools("manage docker containers", top_k=5)

    assert CNT not in discovery["unavailable"]
    tool_hits = {r["tool"]: r for r in discovery["results"] if r.get("kind") == "tool"}
    hit = tool_hits["cm_container_operations"]
    assert hit["description"] == "LIVE manage docker containers"
    assert hit["stale"] is False
