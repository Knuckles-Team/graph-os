"""Unit tests for GRAPHOS-FLEET-R009.1: the capacity-acquisition primitive.

Covers the acceptance clause directly: a denied acquisition allows exactly
one re-decision (a second denial is refused, not retried indefinitely), and
a stopped run's released capacity is available for reuse.
"""

from __future__ import annotations

import pytest

from graph_os.control_plane.runs.capacity import (
    CapacityError,
    CapacityLedger,
    CapacityReDecisionExhaustedError,
)


def test_acquire_grants_all_or_nothing_within_total() -> None:
    ledger = CapacityLedger(total=2)

    decision = ledger.acquire("run-1", amount=2)

    assert decision.granted is True
    assert decision.amount == 2
    assert ledger.available == 0
    assert ledger.held_by("run-1") == 2


def test_acquire_denies_rather_than_partially_grant() -> None:
    ledger = CapacityLedger(total=1)
    ledger.acquire("run-1", amount=1)

    decision = ledger.acquire("run-2", amount=1)

    assert decision.granted is False
    assert decision.amount == 0
    assert ledger.held_by("run-2") == 0
    assert ledger.available == 0


def test_denial_allows_exactly_one_redecision_then_refuses() -> None:
    ledger = CapacityLedger(total=1)
    ledger.acquire("holder-a", amount=1)

    first = ledger.acquire("holder-b", amount=1)
    assert first.granted is False

    # The re-decision: still denied (holder-a still holds the only slot),
    # so the one allowed re-decision is now spent and this call refuses
    # rather than granting a further retry.
    with pytest.raises(CapacityReDecisionExhaustedError):
        ledger.acquire("holder-b", amount=1)

    # A third call for the same holder is refused again, not re-tried.
    with pytest.raises(CapacityReDecisionExhaustedError):
        ledger.acquire("holder-b", amount=1)


def test_redecision_can_grant_once_capacity_frees_up() -> None:
    ledger = CapacityLedger(total=1)
    ledger.acquire("holder-a", amount=1)
    denied = ledger.acquire("holder-b", amount=1)
    assert denied.granted is False

    ledger.release("holder-a")
    redecision = ledger.acquire("holder-b", amount=1)

    assert redecision.granted is True
    assert redecision.redecision is True


def test_stopped_run_release_returns_capacity_for_reuse() -> None:
    ledger = CapacityLedger(total=1)
    ledger.acquire("run-1", amount=1)
    assert ledger.available == 0

    released = ledger.release("run-1")

    assert released == 1
    assert ledger.available == 1
    assert ledger.held_by("run-1") == 0

    decision = ledger.acquire("run-2", amount=1)
    assert decision.granted is True


def test_release_is_idempotent_for_a_holder_with_nothing_held() -> None:
    ledger = CapacityLedger(total=1)

    assert ledger.release("never-acquired") == 0
    assert ledger.available == 1


def test_invalid_amount_rejected() -> None:
    ledger = CapacityLedger(total=1)

    with pytest.raises(CapacityError):
        ledger.acquire("run-1", amount=0)


def test_negative_total_rejected() -> None:
    with pytest.raises(CapacityError):
        CapacityLedger(total=-1)
