"""GRAPHOS-FLEET-R009.1: all-or-nothing capacity acquisition."""

from __future__ import annotations

import pytest

from graph_os.control_plane.runs.admission import (
    CapacityAcquisition,
    CapacityDeniedError,
)


@pytest.mark.spec("GRAPHOS-FLEET-R009.1")
def test_acquire_grants_all_or_nothing() -> None:
    capacity = CapacityAcquisition(capacity={"gpu": 2, "slots": 1})

    granted = capacity.acquire("run-1", {"gpu": 2, "slots": 1})

    assert granted is True
    assert capacity.held("run-1") == {"gpu": 2, "slots": 1}


@pytest.mark.spec("GRAPHOS-FLEET-R009.1")
def test_denial_grants_exactly_one_redecision_then_fails_closed() -> None:
    capacity = CapacityAcquisition(capacity={"gpu": 1})

    first = capacity.acquire("run-1", {"gpu": 2})
    assert first is False
    assert capacity.held("run-1") == {}

    with pytest.raises(CapacityDeniedError):
        capacity.acquire("run-1", {"gpu": 2})


@pytest.mark.spec("GRAPHOS-FLEET-R009.1")
def test_partial_unavailability_holds_nothing() -> None:
    capacity = CapacityAcquisition(capacity={"gpu": 2, "slots": 0})

    granted = capacity.acquire("run-1", {"gpu": 1, "slots": 1})

    assert granted is False
    assert capacity.held("run-1") == {}
    # Nothing was committed: a fresh request for the same gpu unit succeeds.
    assert capacity.acquire("run-2", {"gpu": 2}) is True


@pytest.mark.spec("GRAPHOS-FLEET-R009.1")
def test_stopped_run_releases_held_capacity() -> None:
    capacity = CapacityAcquisition(capacity={"gpu": 1})

    assert capacity.acquire("run-1", {"gpu": 1}) is True
    assert capacity.acquire("run-2", {"gpu": 1}) is False

    capacity.release("run-1")

    assert capacity.held("run-1") == {}
    assert capacity.acquire("run-2", {"gpu": 1}) is True


@pytest.mark.spec("GRAPHOS-FLEET-R009.1")
def test_release_resets_the_redecision_budget_even_with_nothing_held() -> None:
    capacity = CapacityAcquisition(capacity={"gpu": 1})

    assert capacity.acquire("run-1", {"gpu": 1}) is True
    assert capacity.acquire("run-2", {"gpu": 2}) is False  # consumes run-2's redecision

    capacity.release("run-2")  # a stop/reset clears the budget though nothing was held

    # The budget is restored: the next denial is a fresh first denial, not a
    # second one, so it still returns False instead of raising.
    assert capacity.acquire("run-2", {"gpu": 2}) is False
    with pytest.raises(CapacityDeniedError):
        capacity.acquire("run-2", {"gpu": 2})
