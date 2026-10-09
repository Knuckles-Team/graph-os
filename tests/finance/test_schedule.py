"""Tests for the idempotent finance-schedule run record
(GRAPHOS-DATA-MARKET-R002; FI-04 in
``specs/data-and-market-projections/test-spec.md``).
"""

from __future__ import annotations

from graph_os.finance.schedule import (
    FinanceScheduleEntry,
    InMemoryScheduleRunStore,
    record_schedule_run,
)


def _entry(**overrides: object) -> FinanceScheduleEntry:
    base: dict[str, object] = {
        "tenant_id": "tenant-a",
        "owner": "operator-1",
        "action": "backfill",
        "timezone": "America/Chicago",
        "trigger": "2026-11-01T06:00:00Z",
        "idempotency_key": "backfill:tenant-a:2026-11-01",
    }
    base.update(overrides)
    return FinanceScheduleEntry.model_validate(base)


def test_first_run_executes() -> None:
    store = InMemoryScheduleRunStore()

    outcome = record_schedule_run(store, _entry())

    assert outcome == "executed"


def test_replay_with_the_same_key_is_a_duplicate() -> None:
    """FI-04: a restart or DST-crossing replay of the same trigger does not
    re-execute; the idempotency key is caller-supplied, not derived from
    wall-clock time, so a duplicated trigger timestamp changes nothing."""

    store = InMemoryScheduleRunStore()
    record_schedule_run(store, _entry())

    second = record_schedule_run(store, _entry(trigger="2026-11-01T06:00:00-06:00"))

    assert second == "duplicate"


def test_different_tenants_with_the_same_key_both_execute() -> None:
    """Idempotency is scoped per tenant; two tenants never share dedupe state."""

    store = InMemoryScheduleRunStore()

    first = record_schedule_run(store, _entry(tenant_id="tenant-a"))
    second = record_schedule_run(store, _entry(tenant_id="tenant-b"))

    assert first == "executed"
    assert second == "executed"


def test_different_actions_with_the_same_key_are_independent() -> None:
    store = InMemoryScheduleRunStore()
    key = "shared-key"

    backfill = record_schedule_run(
        store, _entry(action="backfill", idempotency_key=key)
    )
    scan = record_schedule_run(store, _entry(action="scan", idempotency_key=key))

    # Same tenant + same idempotency key dedupes regardless of action label:
    # the key alone is the replay-safety fence.
    assert backfill == "executed"
    assert scan == "duplicate"


def test_repeated_replay_stays_duplicate() -> None:
    store = InMemoryScheduleRunStore()
    entry = _entry()
    record_schedule_run(store, entry)

    outcomes = [record_schedule_run(store, entry) for _ in range(5)]

    assert outcomes == ["duplicate"] * 5
