"""The local-skill pseudo-server ranks and precedes alongside fleet discovery.

Covers the multiplexer-side half of CONCEPT:AU-KG.retrieval.unified-capability-
contract for :mod:`graph_os.fleet.local_skill_catalog`: ``discover_tools``
folds :meth:`MCPMultiplexer._local_skill_probe_info` into the SAME probe dict
a fleet harvest populates, so a skill served from a package installed in this
process ranks with fleet tools, and a name collision with a fleet-harvested
skill resolves to the local one. ``graph_os.fleet.local_skill_catalog``'s own
discovery/parsing logic is covered in ``tests/fleet/test_local_skill_catalog.py``;
this file only exercises the merge, ranking and precedence point.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import graph_os.fleet.multiplexer as multiplexer_module
from graph_os.fleet.local_skill_catalog import LOCAL_SKILLS_SERVER
from tests.fleet.test_multiplexer_dynamic_gateway import (
    CNT,
    CNT_TOOL,
    _mux_with_children,
)


def _fixture_local_catalog(entries):
    return lambda: (entries, [])


async def test_discover_tools_ranks_a_local_skill_with_fleet_tools(
    tmp_path, monkeypatch
):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "manage docker containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    mux._probe_cache[CNT] = {
        "tools": [
            {
                "name": CNT_TOOL,
                "description": "manage docker containers",
                "inputSchema": {},
            }
        ],
        "skills": [],
        "error": None,
    }
    monkeypatch.setattr(
        multiplexer_module,
        "build_local_skill_catalog",
        _fixture_local_catalog(
            [
                {
                    "name": "local-onboarding",
                    "uri": "skill://local-onboarding/SKILL.md",
                    "description": "onboard locally, no child process needed",
                    "instructions": "# local onboarding\n\nbody",
                }
            ]
        ),
    )

    discovery = await mux.discover_tools("onboard locally", top_k=5)
    results = discovery["results"]
    kinds = {r["kind"] for r in results}

    assert kinds == {"skill"}
    skill_hit = next(r for r in results if r["kind"] == "skill")
    assert skill_hit["server"] == LOCAL_SKILLS_SERVER
    assert skill_hit["bind"] == {
        "tool_server": LOCAL_SKILLS_SERVER,
        "skill_name": "local-onboarding",
    }
    # "Loading" a skill returns its SKILL.md body exactly as for a harvested
    # one: the ranked row's own probe entry already carries it.
    local_info = mux._local_skill_probe_info()
    loaded = next(s for s in local_info["skills"] if s["name"] == "local-onboarding")
    assert loaded["instructions"] == "# local onboarding\n\nbody"


async def test_local_skill_result_is_cached_and_reused_across_calls(
    tmp_path, monkeypatch
):
    """The local catalog is built once, not re-scanned on every call."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "manage docker containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    mux._probe_cache[CNT] = {"tools": [], "skills": [], "error": None}
    calls = {"count": 0}

    def _counting_catalog():
        calls["count"] += 1
        return [], []

    monkeypatch.setattr(
        multiplexer_module, "build_local_skill_catalog", _counting_catalog
    )

    await mux.discover_tools("anything", top_k=5)
    await mux.discover_tools("anything else", top_k=5)

    assert calls["count"] == 1


async def test_local_skill_shadows_a_fleet_harvested_skill_of_the_same_name(
    tmp_path, monkeypatch
):
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "manage docker containers")]})
    mux._kg_call = AsyncMock(return_value=None)
    mux._probe_cache[CNT] = {
        "tools": [],
        "skills": [
            {
                "name": "shared-skill-name",
                "uri": "skill://shared-skill-name/SKILL.md",
                "description": "the fleet-harvested version",
                "instructions": "harvested body",
            }
        ],
        "error": None,
    }
    monkeypatch.setattr(
        multiplexer_module,
        "build_local_skill_catalog",
        _fixture_local_catalog(
            [
                {
                    "name": "shared-skill-name",
                    "uri": "skill://shared-skill-name/SKILL.md",
                    "description": "the local, installed-package version",
                    "instructions": "local body",
                }
            ]
        ),
    )

    discovery = await mux.discover_tools("shared-skill-name", top_k=5)
    skill_hits = [r for r in discovery["results"] if r["kind"] == "skill"]

    assert len(skill_hits) == 1
    assert skill_hits[0]["server"] == LOCAL_SKILLS_SERVER
    assert skill_hits[0]["description"] == "the local, installed-package version"


async def test_catalog_fleet_probe_lists_local_skills_without_a_query(
    tmp_path, monkeypatch
):
    """``list_catalog`` (no search term) still shows the local skill set — the
    always-offered core pack is a subset of exactly this unconditional set."""
    mux = _mux_with_children(tmp_path, {CNT: [(CNT_TOOL, "manage docker containers")]})
    monkeypatch.setattr(
        multiplexer_module,
        "build_local_skill_catalog",
        _fixture_local_catalog(
            [
                {
                    "name": "always-there",
                    "uri": "skill://always-there/SKILL.md",
                    "description": "offered without searching",
                    "instructions": "body",
                }
            ]
        ),
    )

    probe = await mux._catalog_fleet_probe(include_tools=False)

    assert probe[LOCAL_SKILLS_SERVER]["skills"][0]["name"] == "always-there"
