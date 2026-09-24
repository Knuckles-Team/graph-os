"""``graph_finance``: flip alerts, the scan schedule and live-order proposals.

The MCP tool and its action-routed REST twin ``POST /graph/finance`` run as
the verified tool session on the tenant's session-routed EG client. There is
deliberately no ``approve`` action: a live-order proposal is decided only at
the operator console (``POST /finance/orders/{approve,deny}``,
:mod:`graph_os.gateway.finance_orders`), never by a tool an agent can call.
Everything a caller reads here is informational and grants no authority.

Actions:

* ``subscribe`` / ``unsubscribe`` / ``subscriptions`` / ``alerts`` -- durable
  ``finance.flip`` subscriptions and the caller's inbox (EH-416).
* ``track`` / ``untrack`` / ``tracked`` / ``backfill`` / ``scan`` -- what the
  schedule keeps current, a full backfill now, and the latest-state scanner
  view (EH-419).
* ``explain_flip`` -- the flip explainer: the math first, then claims that
  cite gathered news (EH-419).
* ``propose_order`` / ``order_status`` -- a pending live-order approval and
  its state (EH-423).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from graph_os.finance import delivery, orders, scheduler, subscriptions
from graph_os.finance.models import (
    INFORMATIONAL_NOTICE,
    FlipFilter,
    OrderIntent,
    TrackedSeries,
    principal_ref,
)
from graph_os.mcp_server import runtime

__all__ = ["FinanceToolRequest", "handle_finance", "register_finance_tools"]

TOOL_NAME = "graph_finance"

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

    @property
    def owner(self) -> str:
        return principal_ref(str(self.claims["principal"]))

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
        call.client, call.owner, call.request.filter or FlipFilter(), call.now_ms
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
    return await scheduler.track(call.client, call.required("series"), call.now_ms)


async def _untrack(call: _Call) -> Any:
    return {"stopped": await scheduler.untrack(call.client, call.required("series"))}


async def _tracked(call: _Call) -> Any:
    return [
        s.model_dump(mode="json") for s in await scheduler.tracked_series(call.client)
    ]


async def _backfill(call: _Call) -> Any:
    from graph_os.finance.sources import ServedBarSource

    series = call.required("series")
    records = await scheduler.refresh_series(
        call.client, ServedBarSource(), series, call.now_ns, backfill=True
    )
    report = await scheduler.scan_series(call.client, series, records, call.now_ns)
    return {"bars": len(records), **report}


async def _scan(call: _Call) -> Any:
    states = [record["state"] for record in await scheduler.signal_states(call.client)]
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
    )


async def _order_status(call: _Call) -> Any:
    return await orders.order_status(
        call.client, str(call.claims["tenant"]), call.required("approval_id")
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


async def handle_finance(session: Any, request: FinanceToolRequest) -> Any:
    """Run one operation as the verified ``session``."""
    client = runtime.graph_client(str(session.tenant))
    claims = session.engine_verified_context()
    with client.use_verified_context(claims):
        call = _Call(client, claims, request, time.time_ns())
        return await _HANDLERS[request.action](call)


_DESCRIPTION = (
    "Market trend flips and live-order proposals. Actions: subscribe (filter: "
    "asset_class, timeframe, listing_id, direction), unsubscribe "
    "(subscription_id), subscriptions, alerts (your flip inbox), track / "
    "untrack (series), tracked, backfill (series), scan (scan_filter), "
    "explain_flip (record_id: the math, then source-cited claims), "
    "propose_order (intent, reason) and order_status (approval_id). A proposal "
    "places nothing: a different person approves or denies it at the operator "
    "console. Informational only; alerts and scans authorise no order."
)


async def graph_finance(request: FinanceToolRequest) -> Any:
    """The finance tool: runs as the verified tool session."""
    with runtime.verified_tool_session_scope() as session:
        return await handle_finance(session, request)


def register_finance_tools(mcp: Any) -> None:
    """Register the finance tool and its REST twin."""
    tags = {"graph-os", "finance"}
    mcp.tool(name=TOOL_NAME, description=_DESCRIPTION, tags=tags)(graph_finance)
    runtime.REGISTERED_TOOLS[TOOL_NAME] = graph_finance
