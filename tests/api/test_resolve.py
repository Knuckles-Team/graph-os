"""GRAPHOS-OPS-R012 / GRAPHOS-FLEET-R014: bounded resolver for NL operation lookup.

Covers combined lexical/semantic ranking order, the bounded result cache,
the read-only preview fallback for an unmatched ``ask`` call (never a direct
mutation), and (GRAPHOS-FLEET-R014) that ``record_execution``'s outcome
feedback is an EMA partitioned by the caller's opaque ``scope_ref`` so one
tenant/policy partition's learned reward never leaks into another's rank.
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


@pytest.mark.spec("GRAPHOS-FLEET-R014")
def test_outcome_feedback_is_partitioned_by_scope_ref() -> None:
    """A recorded outcome under one tenant/policy scope never leaks into another's rank.

    ``record_execution`` folds an EMA of observed success into the next
    ``rank`` call for the *same* ``scope_ref``, but a different tenant or
    policy revision (a different opaque ``scope_ref``) must see the
    unbiased baseline score, not the first partition's learned reward.
    """

    resolver = IntentResolver()
    scope_a = resolver.scope_ref("tenant-a", "policy-1")
    scope_b = resolver.scope_ref("tenant-b", "policy-1")

    baseline = resolver.rank(
        "act", "restart the deploy", _DESCRIPTORS, scope_ref=scope_a
    )
    baseline_score = next(
        c.score for c in baseline if c.descriptor.id == "deploy.restart"
    )

    for _ in range(5):
        resolver.record_execution(
            scope_ref=scope_a, verb="act", op="deploy.restart", success=True
        )

    scope_a_ranked = resolver.rank(
        "act", "restart the deploy", _DESCRIPTORS, scope_ref=scope_a
    )
    scope_a_score = next(
        c.score for c in scope_a_ranked if c.descriptor.id == "deploy.restart"
    )
    scope_b_ranked = resolver.rank(
        "act", "restart the deploy", _DESCRIPTORS, scope_ref=scope_b
    )
    scope_b_score = next(
        c.score for c in scope_b_ranked if c.descriptor.id == "deploy.restart"
    )

    assert scope_a_score > baseline_score, (
        "repeated successful outcomes must raise the score for their own scope_ref"
    )
    assert scope_b_score < scope_a_score, (
        "a different tenant/policy scope_ref must not see scope_a's learned reward"
    )


@pytest.mark.spec("GRAPHOS-FLEET-R014")
def test_scope_ref_requires_verified_tenant_and_policy_revision() -> None:
    resolver = IntentResolver()
    with pytest.raises(ValueError):
        resolver.scope_ref("", "policy-1")
    with pytest.raises(ValueError):
        resolver.scope_ref("tenant-a", "")
