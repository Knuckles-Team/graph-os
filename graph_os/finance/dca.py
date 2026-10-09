"""Recurring DCA plan model and non-duplicating proposal key (GRAPHOS-DATA-MARKET-R006, FI-10).

``DCAPlan`` is the typed, durable record of a recurring dollar-cost-averaging
plan: owner, tenant, symbol, account binding, decimal amount and currency,
timezone, recurrence rule, bounds, pause state, plan revision, and paper or
live mode. ``build_dca_proposal_key`` derives one deterministic proposal key
per due occurrence from the plan's revision, so replay or clock drift can
never produce two proposals for the same occurrence. ``record_dca_proposal``
is the dedupe core, mirroring ``record_schedule_run``. Binding live-mode
proposals to human approval and the AU-CONTEXT-R006 connector write-back
lease, and reporting a missed contribution, is GDM-07a follow-on work; this
module owns only the plan model and the proposal dedupe record.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DCAPlan",
    "DCAProposalOutcome",
    "DCAProposalStore",
    "InMemoryDCAProposalStore",
    "PlanMode",
    "build_dca_proposal_key",
    "record_dca_proposal",
]

PlanMode = Literal["paper", "live"]
DCAProposalOutcome = Literal["proposed", "duplicate"]


class DCAPlan(BaseModel):
    """One recurring dollar-cost-averaging plan."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=0)
    owner: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    symbol: str = Field(min_length=1, max_length=32)
    account_id: str = Field(min_length=1, max_length=256)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    timezone: str = Field(min_length=1, max_length=64, description="IANA zone.")
    recurrence_rule: str = Field(min_length=1, max_length=256)
    paused: bool = False
    mode: PlanMode = "paper"


def build_dca_proposal_key(plan: DCAPlan, occurrence: str) -> str:
    """Derive one deterministic proposal key for a due occurrence.

    The key is derived from the plan's revision, not wall-clock time, so a
    revised plan's earlier proposals never collide with its later ones, and
    replaying the same occurrence under the same revision always derives the
    same key.
    """

    return f"{plan.plan_id}:{plan.revision}:{occurrence}"


class DCAProposalStore(Protocol):
    """Durable (tenant_id, proposal_key) membership."""

    def seen(self, tenant_id: str, proposal_key: str) -> bool:
        """True when this tenant already recorded this proposal key."""

    def record(self, tenant_id: str, proposal_key: str) -> None:
        """Durably record that this tenant proposed this occurrence."""


class InMemoryDCAProposalStore:
    """Reference ``DCAProposalStore`` for tests and a single-process default."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def seen(self, tenant_id: str, proposal_key: str) -> bool:
        return (tenant_id, proposal_key) in self._seen

    def record(self, tenant_id: str, proposal_key: str) -> None:
        self._seen.add((tenant_id, proposal_key))


def record_dca_proposal(
    store: DCAProposalStore, plan: DCAPlan, occurrence: str
) -> DCAProposalOutcome:
    """Propose one DCA occurrence exactly once per plan revision.

    FI-10: replaying the same ``occurrence`` under the same plan revision --
    whether from clock drift or a retried trigger -- returns ``"proposed"``
    once and ``"duplicate"`` on every subsequent call.
    """

    key = build_dca_proposal_key(plan, occurrence)
    if store.seen(plan.tenant_id, key):
        return "duplicate"
    store.record(plan.tenant_id, key)
    return "proposed"
