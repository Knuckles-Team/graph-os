"""GRAPHOS-CAPACITY-R001.1: pure partitioned AIMD controller.

Covers :mod:`graph_os.fleet.error_budget` -- bounded additive recovery,
multiplicative decrease on budget breach, the engine/policy ceiling, caller
error/policy denial neutrality, idempotent replay, and tenant/child
partition isolation, matching the fixtures in
``specs/adaptive-capacity/test-spec.md``.
"""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.fleet.error_budget import (
    AimdConfig,
    BudgetWindow,
    OutcomeClass,
    OutcomeSample,
    Partition,
    ThrottleMode,
    decide,
)
from tests.fleet._support import CAPACITY_CONFIG as _CONFIG
from tests.fleet._support import CAPACITY_PARTITION as _PARTITION

pytestmark = pytest.mark.spec("GRAPHOS-CAPACITY-R001.1")


def _window(window_id: str, outcomes: list[OutcomeClass]) -> BudgetWindow:
    return BudgetWindow(
        window_id=window_id,
        partition=_PARTITION,
        samples=tuple(OutcomeSample(outcome) for outcome in outcomes),
    )


def test_healthy_window_increases_additively_and_never_above_headroom() -> None:
    window = _window("w1", [OutcomeClass.SUCCESS] * 10)
    decision = decide(
        window=window,
        prior_limit=8,
        engine_headroom=9,
        config=_CONFIG,
        mode=ThrottleMode.OBSERVE,
    )
    assert decision.reason == "healthy_window_increase"
    assert decision.new_limit == 9  # capped at headroom, not 8 + alpha
    assert decision.mode is ThrottleMode.OBSERVE


def test_budget_breach_decreases_multiplicatively_never_below_floor() -> None:
    outcomes = [OutcomeClass.RETRYABLE_SERVICE_ERROR] * 5 + [OutcomeClass.SUCCESS] * 5
    window = _window("w2", outcomes)
    decision = decide(
        window=window,
        prior_limit=10,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    assert decision.reason == "budget_breach_decrease"
    assert decision.new_limit == 5  # floor(10 * 0.5)
    assert decision.new_limit >= _CONFIG.floor


def test_breach_never_drops_below_configured_floor() -> None:
    outcomes = [OutcomeClass.TIMEOUT] * 8
    window = _window("w3", outcomes)
    decision = decide(
        window=window,
        prior_limit=1,
        engine_headroom=5,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    assert decision.new_limit == _CONFIG.floor


def test_caller_errors_and_policy_denials_never_count_as_child_unhealthy() -> None:
    outcomes = (
        [OutcomeClass.PERMANENT_CALLER_ERROR] * 20
        + [OutcomeClass.POLICY_DENIAL] * 20
        + [OutcomeClass.SUCCESS] * 4
    )
    window = _window("w4", outcomes)
    decision = decide(
        window=window,
        prior_limit=8,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.OBSERVE,
    )
    assert decision.sample_count == 4
    assert decision.eligible_failure_count == 0
    assert decision.reason == "healthy_window_increase"


def test_too_few_eligible_samples_moves_nothing() -> None:
    window = _window("w5", [OutcomeClass.SUCCESS, OutcomeClass.TIMEOUT])
    decision = decide(
        window=window,
        prior_limit=6,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.OBSERVE,
    )
    assert decision.reason == "insufficient_samples"
    assert decision.new_limit == 6


def test_engine_ceiling_narrows_immediately_even_mid_recovery() -> None:
    window = _window("w6", [OutcomeClass.SUCCESS] * 10)
    decision = decide(
        window=window,
        prior_limit=15,
        engine_headroom=3,
        config=_CONFIG,
        mode=ThrottleMode.OBSERVE,
    )
    assert decision.new_limit == 3


def test_replaying_the_same_window_is_idempotent() -> None:
    window = _window("w7", [OutcomeClass.TIMEOUT] * 8)
    first = decide(
        window=window,
        prior_limit=10,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    second = decide(
        window=window,
        prior_limit=10,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    assert first == second
    assert first.window_digest == second.window_digest


def test_partitions_never_cross_tenant_or_child() -> None:
    other = Partition(
        tenant="t2", child="search", operation_class="read", policy_revision="p1"
    )
    window_a = _window("w8", [OutcomeClass.TIMEOUT] * 8)
    window_b = BudgetWindow(
        window_id="w8-other",
        partition=other,
        samples=tuple(OutcomeSample(OutcomeClass.SUCCESS) for _ in range(8)),
    )
    decision_a = decide(
        window=window_a,
        prior_limit=10,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    decision_b = decide(
        window=window_b,
        prior_limit=10,
        engine_headroom=20,
        config=_CONFIG,
        mode=ThrottleMode.ENFORCE,
    )
    assert decision_a.partition.tenant == "t1"
    assert decision_b.partition.tenant == "t2"
    assert decision_a.new_limit != decision_b.new_limit
    assert decision_a.window_digest != decision_b.window_digest


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alpha": 0},
        {"beta": 0},
        {"beta": 1},
        {"floor": -1},
        {"min_sample_count": 0},
        {"error_budget_fraction": 0},
        {"error_budget_fraction": 1},
        {"version": ""},
    ],
)
def test_config_rejects_out_of_range_tuning(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "version": "v1",
        "alpha": 2,
        "beta": 0.5,
        "floor": 1,
        "min_sample_count": 4,
        "error_budget_fraction": 0.2,
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        AimdConfig(**base)


def test_partition_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        Partition(tenant="", child="c", operation_class="read", policy_revision="p1")


def test_window_requires_an_id() -> None:
    with pytest.raises(ValueError):
        BudgetWindow(window_id="", partition=_PARTITION, samples=())
