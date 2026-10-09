"""Covers :mod:`graph_os.fleet.throttle_service` -- the in-process read/config
facade over recorded AIMD decisions, backing the GRAPHOS-CAPACITY-R002
hosted operations.
"""

from __future__ import annotations

from graph_os.fleet.error_budget import (
    AimdConfig,
    BudgetWindow,
    OutcomeClass,
    OutcomeSample,
    Partition,
    ThrottleMode,
    decide,
)
from graph_os.fleet.throttle_service import ThrottleRegistry

_PARTITION = Partition(
    tenant="t1", child="search", operation_class="read", policy_revision="p1"
)
_CONFIG = AimdConfig(
    version="v1",
    alpha=2,
    beta=0.5,
    floor=1,
    min_sample_count=4,
    error_budget_fraction=0.2,
)


def test_unknown_partition_reports_no_decision_not_fabricated_health() -> None:
    registry = ThrottleRegistry()
    status = registry.status(_PARTITION)
    assert status.current_limit is None
    assert status.reason == "no_decision_recorded"
    assert status.last_decision is None
    assert status.mode is ThrottleMode.OBSERVE


def test_recorded_decision_is_readable_by_partition() -> None:
    registry = ThrottleRegistry()
    window = BudgetWindow(
        window_id="w1",
        partition=_PARTITION,
        samples=tuple(OutcomeSample(OutcomeClass.SUCCESS) for _ in range(10)),
    )
    decision = decide(
        window=window,
        prior_limit=5,
        engine_headroom=10,
        config=_CONFIG,
        mode=ThrottleMode.OBSERVE,
    )
    registry.record_decision(decision)
    status = registry.status(_PARTITION)
    assert status.current_limit == decision.new_limit
    assert status.reason == decision.reason
    assert status.last_decision == decision


def test_set_mode_is_read_back_and_defaults_to_observe() -> None:
    registry = ThrottleRegistry()
    assert registry.mode(_PARTITION) is ThrottleMode.OBSERVE
    registry.set_mode(_PARTITION, ThrottleMode.ENFORCE)
    assert registry.mode(_PARTITION) is ThrottleMode.ENFORCE
    assert registry.status(_PARTITION).mode is ThrottleMode.ENFORCE


def test_partitions_are_isolated() -> None:
    other = Partition(
        tenant="t2", child="search", operation_class="read", policy_revision="p1"
    )
    registry = ThrottleRegistry()
    registry.set_mode(_PARTITION, ThrottleMode.ENFORCE)
    assert registry.mode(other) is ThrottleMode.OBSERVE
    assert registry.status(other).current_limit is None
