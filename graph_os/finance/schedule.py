"""Idempotent finance-schedule run record (GRAPHOS-DATA-MARKET-R002, FI-04).

``FinanceScheduleEntry`` records one backfill/scan/DCA-due/explanation job's
tenant, owner, authorized action, timezone, trigger, and idempotency key, per
FR-08. ``record_schedule_run`` is the replay-safety core FI-04 tests: the
same ``(tenant_id, idempotency_key)`` pair yields exactly one ``"executed"``
outcome no matter how many times it is replayed -- a daylight-saving
transition or a restart that replays the same trigger cannot duplicate an
effect, because the key is caller-supplied and never derived from wall-clock
time. Registering these entries against ``graph_os.gateway.daemon``'s
maintenance-scheduler tick (the "existing scheduler" FR-08 names) is GDM-07
follow-on work; this module owns only the dedupe record.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FinanceScheduleEntry",
    "InMemoryScheduleRunStore",
    "RunOutcome",
    "ScheduleAction",
    "ScheduleRunStore",
    "record_schedule_run",
]

ScheduleAction = Literal["backfill", "scan", "dca_due", "explain"]
RunOutcome = Literal["executed", "duplicate"]


class FinanceScheduleEntry(BaseModel):
    """One scheduled finance job occurrence."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=256)
    owner: str = Field(min_length=1, max_length=256)
    action: ScheduleAction
    timezone: str = Field(min_length=1, max_length=64, description="IANA zone.")
    trigger: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)


class ScheduleRunStore(Protocol):
    """Durable (tenant_id, idempotency_key) membership, supplied by the caller."""

    def seen(self, tenant_id: str, idempotency_key: str) -> bool:
        """True when this tenant already recorded this idempotency key."""

    def record(self, tenant_id: str, idempotency_key: str) -> None:
        """Durably record that this tenant ran this idempotency key."""


class InMemoryScheduleRunStore:
    """Reference ``ScheduleRunStore`` for tests and a single-process default."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def seen(self, tenant_id: str, idempotency_key: str) -> bool:
        return (tenant_id, idempotency_key) in self._seen

    def record(self, tenant_id: str, idempotency_key: str) -> None:
        self._seen.add((tenant_id, idempotency_key))


def record_schedule_run(
    store: ScheduleRunStore, entry: FinanceScheduleEntry
) -> RunOutcome:
    """Record one schedule occurrence; a replayed key never re-executes.

    FI-04: calling this twice with the same ``(tenant_id, idempotency_key)``
    -- whether from a daylight-saving transition's duplicated trigger or a
    restart's replay -- returns ``"executed"`` once and ``"duplicate"`` on
    every subsequent call, with no write beyond the first ``record``.
    """

    if store.seen(entry.tenant_id, entry.idempotency_key):
        return "duplicate"
    store.record(entry.tenant_id, entry.idempotency_key)
    return "executed"
