"""Privacy-safe operation audit payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from graph_os.api.invoke.plan import params_digest
from graph_os.api.invoke.steps import OpError, OpResult, VerifiedCaller


def audit_event(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    surface: Any,
    status: str,
    audit_ref: str = "",
) -> dict[str, str]:
    """Record identity and a digest, never argument values."""

    event = {
        "op": op.id,
        "surface": surface.value,
        "principal": caller.principal,
        "tenant": caller.tenant,
        "plan_digest": params_digest(params),
        "result_status": status,
        "request_id": caller.request_id,
    }
    if audit_ref:
        event["audit_ref"] = audit_ref
    if op.id == "fleet.call":
        server, tool = params.get("server"), params.get("tool")
        if isinstance(server, str) and isinstance(tool, str):
            event["target"] = f"{server}/{tool}"
    if getattr(op.executor, "value", None) == "service":
        event["owner"] = caller.principal
    return event


@dataclass(frozen=True, slots=True)
class EffectReservation:
    """Durable atomic claim; only acquired may dispatch. No local fallback."""

    reference: str
    state: Literal["acquired", "pending", "completed", "conflict"]
    outcome: OpResult | OpError | None = None


class EffectJournal(Protocol):
    """Owner port for durable deduplication, shared across transports/processes.

    reserve atomically binds a stable tenant/principal/op/idempotency key to
    the request/authority digest, returns completed outcomes for replays, and
    grants at most one acquisition. A changed binding conflicts, never acquires.
    Pending/uncertain effects never reacquire. complete is fenced by the opaque
    acquisition reference, atomically persists an outcome, accepts identical
    completion retries, and rejects conflicting terminal outcomes. A completion
    failure or cancellation leaves the reservation pending for reconciliation.
    This is an existing durable effect owner's port, not a GraphOS ledger.
    """

    async def reserve(self, key: str, binding: str) -> EffectReservation: ...

    async def complete(self, reference: str, outcome: OpResult | OpError) -> None: ...
