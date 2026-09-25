"""Finance operations on the shared invoke authority path (MCPI-13)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Executor,
    Idempotency,
    OpSpec,
    PrincipalRule,
    SubjectRef,
    Surface,
    Verb,
)
from graph_os.finance.authority import ACTION_SCOPES
from graph_os.finance.models import FlipFilter, OrderIntent, TrackedSeries
from graph_os.finance.orders import OrderDecision


class FinanceParams(BaseModel):
    """Closed action input; tenant authority comes from verified caller context."""

    model_config = ConfigDict(extra="forbid")
    filter: FlipFilter | None = None
    subscription_id: str = Field(default="", max_length=64)
    series: TrackedSeries | None = None
    scan_filter: dict[str, Any] = Field(default_factory=dict)
    record_id: str = Field(default="", max_length=128)
    intent: OrderIntent | None = None
    reason: str = Field(default="", max_length=2048)
    approval_id: str = Field(default="", max_length=64)
    limit: int = Field(default=50, ge=1, le=500)


class DecisionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(pattern=r"^finance_order:[0-9a-f]{32}$")
    intent_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class FinanceResult(BaseModel):
    model_config = ConfigDict(extra="allow")


_NAMES = {
    "subscribe": "alerts.subscribe",
    "unsubscribe": "alerts.unsubscribe",
    "subscriptions": "alerts.list",
    "alerts": "alerts.inbox",
    "track": "track.start",
    "untrack": "track.stop",
    "tracked": "track.list",
    "backfill": "backfill",
    "scan": "scan",
    "explain_flip": "flip.explain",
    "propose_order": "orders.propose",
    "order_status": "orders.status",
}
_MUTATIONS = frozenset(
    {"subscribe", "unsubscribe", "track", "untrack", "backfill", "propose_order"}
)
_INFRA = {
    "subscribe": frozenset({"node:write", "lease:write"}),
    "unsubscribe": frozenset({"node:write", "lease:write"}),
    "track": frozenset({"node:write", "timeseries:write", "compute:finance"}),
    "untrack": frozenset({"node:write"}),
    "backfill": frozenset({"node:write", "timeseries:write", "compute:finance"}),
    "scan": frozenset({"compute:finance"}),
    "propose_order": frozenset({"lease:write"}),
}


def specs() -> tuple[OpSpec, ...]:
    """Finance user scopes remain separate from service infrastructure grants."""
    items = []
    for action, name in _NAMES.items():
        effect = Effect.WRITE if action in _MUTATIONS else Effect.READ
        items.append(
            OpSpec(
                id=f"finance.{name}",
                verb=Verb.ACT if effect is Effect.WRITE else Verb.ASK,
                summary=f"Finance {name.replace('.', ' ')}",
                examples=(f"Finance {name.replace('.', ' ')}",),
                params=FinanceParams,
                result=FinanceResult,
                binding=Composite(handler="graph_os.api.ops.finance.execute"),
                executor=Executor.SERVICE,
                scopes=frozenset({ACTION_SCOPES[action]}),
                executor_scopes=_INFRA.get(action, frozenset({"node:read"})),
                subject=SubjectRef(path="$caller.tenant"),
                effect=effect,
                idempotency=Idempotency.KEY_REQUIRED
                if effect is Effect.WRITE
                else Idempotency.NONE,
                audit=AuditClass.EVENT if effect is Effect.WRITE else AuditClass.NONE,
            )
        )
    for action in ("approve", "deny"):
        items.append(
            OpSpec(
                id=f"finance.orders.{action}",
                verb=Verb.MANAGE,
                summary=f"{action.title()} an exact live-order proposal at the console",
                examples=(f"{action.title()} a pending live order",),
                params=DecisionParams,
                result=FinanceResult,
                binding=Composite(handler="graph_os.api.ops.finance.decide"),
                scopes=frozenset({"finance:approve-live-order"}),
                effect=Effect.ADMIN,
                principals=PrincipalRule.HUMAN_UNDELEGATED,
                surfaces=frozenset({Surface.CONSOLE}),
                idempotency=Idempotency.KEY_REQUIRED,
                audit=AuditClass.EVENT,
            )
        )
    return tuple(items)


async def execute(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    """Run finance handlers with the invoke executor's service client."""
    from graph_os.finance.finance_ops import _HANDLERS, FinanceToolRequest, _Call

    action = next(
        (key for key, name in _NAMES.items() if op.id == f"finance.{name}"), None
    )
    if action is None or not context.service_identity:
        raise PermissionError("finance service authority is unavailable")
    if ACTION_SCOPES[action] not in context.caller.effective_scopes:
        raise PermissionError("finance domain scope is unavailable")
    request = FinanceToolRequest.model_validate({"action": action, **params})
    claims = dict(context.caller.engine_claims)
    return await _HANDLERS[action](
        _Call(context.client, claims, request, time.time_ns(), context.idempotency_key)
    )


async def decide(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    """The shared invoke pipeline has already enforced console MFA and plan binding."""
    from graph_os.finance.orders import approve_order, deny_order

    if context.service_identity or context.caller.delegated:
        raise PermissionError("a verified human console session is required")
    if "finance:approve-live-order" not in context.caller.effective_scopes:
        raise PermissionError("finance approval scope is required")
    decision = OrderDecision.model_validate(params)
    handler = {
        "finance.orders.approve": approve_order,
        "finance.orders.deny": deny_order,
    }.get(op.id)
    if handler is None:
        raise ValueError("unknown finance decision")
    return await handler(
        context.client,
        dict(context.caller.engine_claims),
        context.caller.effective_scopes,
        decision,
        time.time_ns() // 1_000_000,
    )
