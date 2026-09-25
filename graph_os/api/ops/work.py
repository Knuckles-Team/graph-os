"""Work market operations over EG work items and an admitted market port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


class MarketParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap_id: str | None = None
    offer_id: str | None = None
    item_id: str | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    request: dict[str, object] | None = None


class MarketResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: object


async def market_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    """Call only a composition-root supplied, authority-bound market adapter."""
    market = context.services.get("work_market")
    if market is None or not callable(getattr(market, "execute", None)):
        raise RuntimeError("work market control is unavailable")
    return {"value": await market.execute(op.id, params)}


def _native(name: str, method: str, verb: Verb, effect: Effect, scope: str) -> OpSpec:
    return OpSpec(
        id=f"work.items.{name}",
        verb=verb,
        summary=f"{name.capitalize()} a governed work item",
        examples=(f"{name.capitalize()} this work item",),
        params=EgSchemaRef(
            path=f"contract/schemas/method.request.json#/methods/{method}"
        ),
        result=EgSchemaRef(
            path=f"contract/schemas/result.coordination.json#/methods/{method}"
        ),
        binding=EgMethod(service=method, op=method),
        scopes=frozenset({scope}),
        effect=effect,
        idempotency=Idempotency.NATURAL
        if effect is Effect.READ
        else Idempotency.KEY_REQUIRED,
        audit=AuditClass.NONE if effect is Effect.READ else AuditClass.EVENT,
    )


def _market(name: str, verb: Verb, effect: Effect, scope: str) -> OpSpec:
    return OpSpec(
        id=f"work.{name}",
        verb=verb,
        summary=f"{name.replace('.', ' ')} in the governed work market",
        examples=(f"{name.replace('.', ' ')} for this work market",),
        params=MarketParams,
        result=MarketResult,
        binding=Composite(handler="graph_os.api.ops.work.market_handler"),
        scopes=frozenset({scope}),
        effect=effect,
        idempotency=Idempotency.NONE
        if effect is Effect.READ
        else Idempotency.KEY_REQUIRED,
        audit=AuditClass.NONE if effect is Effect.READ else AuditClass.EVENT,
    )


def specs() -> tuple[OpSpec, ...]:
    """Curated work-market IDs; unbound market controls remain unavailable."""
    return (
        _native("submit", "SubmitWorkItem", Verb.WRITE, Effect.WRITE, "work:submit"),
        _native("get", "GetWorkItem", Verb.ASK, Effect.READ, "work:read"),
        _native("list", "ListWorkItems", Verb.ASK, Effect.READ, "work:read"),
        _native("outcome", "GetWorkItemOutcome", Verb.ASK, Effect.READ, "work:read"),
        _native(
            "cancel", "CancelWorkItem", Verb.MANAGE, Effect.DESTRUCTIVE, "work:write"
        ),
        _market("gaps.list", Verb.ASK, Effect.READ, "gap:read"),
        _market("gaps.get", Verb.ASK, Effect.READ, "gap:read"),
        _market("gaps.submit", Verb.WRITE, Effect.WRITE, "gap:write"),
        _market("offers.list", Verb.ASK, Effect.READ, "work:read"),
        _market("offers.get", Verb.ASK, Effect.READ, "work:read"),
        _market("offers.submit", Verb.WRITE, Effect.WRITE, "work:write"),
        _market("offers.accept", Verb.MANAGE, Effect.ADMIN, "work:write"),
    )
