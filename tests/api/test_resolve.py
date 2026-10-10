"""GRAPHOS-OPS-R012: bounded resolver for natural-language operation lookup.

Covers the three properties the requirement names: combined lexical/semantic
ranking order, the bounded result cache, and the read-only preview fallback
for an unmatched ``ask`` call (never a direct mutation).
"""

from __future__ import annotations

import pytest

from graph_os.api.mcp.resolve import Descriptor, IntentResolver

_DESCRIPTORS = (
    Descriptor(
        id="deploy.restart",
        verb="act",
        summary="Restart a deployed service",
        examples=("restart the service",),
        domain="deploy",
        tags=("deploy", "restart"),
        params_schema={"required": ["service"]},
    ),
    Descriptor(
        id="deploy.status",
        verb="act",
        summary="Check whether a deployment finished",
        examples=("is the deploy done",),
        domain="deploy",
        tags=("deploy", "status"),
        params_schema={},
    ),
)

_UQL_DESCRIPTOR = Descriptor(id="query.uql", verb="ask", summary="Run a UQL query")


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_rank_orders_lexical_name_match_above_description_only_match() -> None:
    resolver = IntentResolver()
    ranked = resolver.rank("act", "restart the deploy", _DESCRIPTORS, scope_ref=None)

    assert [candidate.descriptor.id for candidate in ranked] == [
        "deploy.restart",
        "deploy.status",
    ]
    assert ranked[0].score > ranked[1].score


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_rank_is_cached_for_an_identical_visible_snapshot() -> None:
    resolver = IntentResolver()
    first = resolver.rank("act", "restart the deploy", _DESCRIPTORS, scope_ref=None)
    second = resolver.rank("act", "restart the deploy", _DESCRIPTORS, scope_ref=None)

    assert first is second
    assert len(resolver._cache) == 1


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_cache_evicts_oldest_entry_once_over_its_bound() -> None:
    resolver = IntentResolver(cache_limit=2)
    for n in range(3):
        resolver.rank("act", f"restart deploy number {n}", _DESCRIPTORS, scope_ref=None)

    assert len(resolver._cache) == 2


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_unmatched_ask_falls_back_to_read_only_uql_preview() -> None:
    resolver = IntentResolver()
    resolution = resolver.resolve(
        "ask",
        "why did the deploy fail",
        (*_DESCRIPTORS, _UQL_DESCRIPTOR),
        scope_ref=None,
    )

    assert resolution.fallback is True
    assert resolution.op == "query.uql"
    assert resolution.params["nl"] is True
    assert resolution.preview is False, (
        "ask fallback must never require a mutation plan"
    )


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_unmatched_ask_without_uql_refuses_rather_than_mutating() -> None:
    resolver = IntentResolver()
    resolution = resolver.resolve(
        "ask", "why did the deploy fail", _DESCRIPTORS, scope_ref=None
    )

    assert resolution.op is None
    assert resolution.fallback is False
    assert resolution.preview is False


@pytest.mark.spec("GRAPHOS-OPS-R012")
def test_matched_mutation_verb_always_requires_a_reviewed_preview() -> None:
    resolver = IntentResolver()
    resolution = resolver.resolve(
        "act", "restart the deploy", _DESCRIPTORS, scope_ref=None
    )

    assert resolution.op == "deploy.restart"
    assert resolution.preview is True
