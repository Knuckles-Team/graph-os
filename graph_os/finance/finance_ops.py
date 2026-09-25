"""Finance action handlers shared by all GraphOS operation surfaces.

All caller scope and subject checks happen in the shared invoke pipeline.
The caller identity is taken only from its verified context; work uses only
its service client after that check. This module has no MCP host dependency.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_os.finance import delivery, orders, subscriptions
from graph_os.finance.models import (
    INFORMATIONAL_NOTICE,
    FlipFilter,
    OrderIntent,
    TrackedSeries,
    principal_ref,
)

Action = Literal[
    "subscribe",
    "unsubscribe",
    "subscriptions",
    "alerts",
    "track",
    "untrack",
    "tracked",
    "backfill",
    "scan",
    "explain_flip",
    "propose_order",
    "order_status",
]


class FinanceToolRequest(BaseModel):
    """One finance operation; the fields each action needs."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    filter: FlipFilter | None = None
    subscription_id: str = Field(default="", max_length=64)
    series: TrackedSeries | None = None
    scan_filter: dict[str, Any] = Field(default_factory=dict)
    record_id: str = Field(default="", max_length=128)
    intent: OrderIntent | None = None
    reason: str = Field(default="", max_length=2048)
    approval_id: str = Field(default="", max_length=64)
    limit: int = Field(default=50, ge=1, le=500)


@dataclass(frozen=True, slots=True)
class _Call:
    client: Any
    claims: dict[str, Any]
    request: FinanceToolRequest
    now_ns: int
    idempotency_key: str | None = None

    @property
    def owner(self) -> str:
        return principal_ref(str(self.claims["principal"]))

    @property
    def owner_agent(self) -> str:
        return str(self.claims["agent_id"])

    @property
    def now_ms(self) -> int:
        return self.now_ns // 1_000_000

    def required(self, name: str) -> Any:
        value = getattr(self.request, name)
        if not value:
            raise ValueError(f"{self.request.action} needs {name}")
        return value


async def _subscribe(call: _Call) -> Any:
    return await subscriptions.subscribe(
        call.client,
        call.owner,
        call.owner_agent,
        call.request.filter or FlipFilter(),
        call.now_ms,
    )


async def _unsubscribe(call: _Call) -> Any:
    return await subscriptions.unsubscribe(
        call.client, call.owner, call.required("subscription_id")
    )


async def _subscriptions(call: _Call) -> Any:
    return await subscriptions.active_subscriptions(call.client, call.owner)


async def _alerts(call: _Call) -> Any:
    return await delivery.inbox(call.client, call.owner, limit=call.request.limit)


async def _track(call: _Call) -> Any:
    from graph_os.finance import scheduler

    return await scheduler.track(
        call.client, call.required("series"), call.owner, call.owner_agent, call.now_ms
    )


async def _untrack(call: _Call) -> Any:
    from graph_os.finance import scheduler

    stopped = await scheduler.untrack(call.client, call.required("series"), call.owner)
    return {"stopped": stopped}


async def _tracked(call: _Call) -> Any:
    from graph_os.finance import scheduler

    return [
        s.model_dump(mode="json")
        for s in await scheduler.tracked_by(call.client, call.owner)
    ]


async def _backfill(call: _Call) -> Any:
    from graph_os.finance import scheduler
    from graph_os.finance.catalog import put_catalog
    from graph_os.finance.sources import ServedMarketSource

    series = call.required("series")
    await put_catalog(call.client, series)
    records = await scheduler.refresh_series(
        call.client, ServedMarketSource(), series, call.now_ns, backfill=True
    )
    report = await scheduler.scan_series(call.client, series, records, call.now_ns)
    return {"bars": len(records), **report}


async def _scan(call: _Call) -> Any:
    from graph_os.finance import scheduler

    states = [
        record["checkpoint"] for record in await scheduler.signal_states(call.client)
    ]
    page = await call.client.finance.market(
        "signal_scan",
        request={
            "states": states,
            "filter": call.request.scan_filter,
            "limit": call.request.limit,
        },
    )
    return {"scan": page, "notice": INFORMATIONAL_NOTICE}


async def _explain_flip(call: _Call) -> Any:
    from agent_utilities.api.finance import Evidence, explain_flip

    from graph_os.finance.sources import FleetNewsSource

    record_id = call.required("record_id")
    entries = await delivery.inbox(call.client, call.owner, limit=500)
    alert = next((e["alert"] for e in entries if e.get("record_id") == record_id), None)
    if alert is None:
        raise LookupError("no flip with this record id is in the caller's inbox")
    evidence = [Evidence(**item) for item in await FleetNewsSource().around(alert)]
    return (await explain_flip(alert, evidence)).model_dump(mode="json")


async def _propose_order(call: _Call) -> Any:
    return await orders.propose_order(
        call.client,
        call.claims,
        call.required("intent"),
        call.request.reason,
        call.now_ms,
        call.idempotency_key,
    )


async def _order_status(call: _Call) -> Any:
    return await orders.order_status(
        call.client, call.claims, call.required("approval_id")
    )


_HANDLERS: dict[str, Callable[[_Call], Awaitable[Any]]] = {
    "subscribe": _subscribe,
    "unsubscribe": _unsubscribe,
    "subscriptions": _subscriptions,
    "alerts": _alerts,
    "track": _track,
    "untrack": _untrack,
    "tracked": _tracked,
    "backfill": _backfill,
    "scan": _scan,
    "explain_flip": _explain_flip,
    "propose_order": _propose_order,
    "order_status": _order_status,
}
