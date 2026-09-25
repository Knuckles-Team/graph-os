"""Deliver queued flips into each owner's inbox, exactly once (EH-416).

The broker is at-least-once: a message whose consumer dies between delivery
and ack comes back. Delivery is therefore made idempotent at the sink: the
inbox entry for ``(subscription, flip record)`` is created with EG's atomic
``CreateNodeIfAbsent``, so a redelivered message finds its entry already there,
is counted as a duplicate and acked. A failed write is rejected back to the
queue (and dead-lettered after the queue's attempt limit), never dropped.

Before every delivery the subscriber's CURRENT read authority over the tenant
graph is re-checked with EG (``CheckAccess``): a subscriber whose grant was
revoked receives nothing more -- the message is acknowledged as withheld, so no
data the subscriber cannot read is ever delivered.

An inbox entry is information. It names the flip and the notice; it holds no
order, no approval and no authority.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from graph_os.finance.authority import can_read
from graph_os.finance.store import labelled
from graph_os.finance.subscriptions import active_subscriptions
from graph_os.finance.topic import decode_alert

__all__ = ["ALERT_LABEL", "DrainReport", "drain_all", "drain_subscription", "inbox"]

logger = logging.getLogger(__name__)

ALERT_LABEL = "FinanceFlipAlert"
CONSUMER_GROUP = "graphos-finance-alerts"
#: A claimed message returns to the queue if this process dies holding it.
LEASE_MS = 60_000


@dataclass(slots=True)
class DrainReport:
    delivered: int = 0
    duplicates: int = 0
    failed: int = 0
    withheld: int = 0

    def add(self, other: DrainReport) -> None:
        self.delivered += other.delivered
        self.duplicates += other.duplicates
        self.failed += other.failed
        self.withheld += other.withheld


def _alert_id(subscription_id: str, record_id: str) -> str:
    return f"finance_flip_alert:{subscription_id}:{record_id}"


def _entry(
    subscription: dict[str, Any], alert: dict[str, Any], now_ms: int
) -> dict[str, Any]:
    return {
        "type": ALERT_LABEL,
        "owner": subscription["owner"],
        "subscription_id": subscription["subscription_id"],
        "record_id": alert["record"]["record_id"],
        "alert": alert,
        "delivered_at_ms": now_ms,
        "read": False,
    }


async def _deliver_one(
    client: Any, subscription: dict[str, Any], properties: dict[str, Any], now_ms: int
) -> bool | None:
    """Write the inbox entry: ``False`` when already delivered, ``None`` when
    the subscriber can no longer read the tenant graph (withheld)."""
    if not await can_read(client, str(subscription.get("owner_agent", ""))):
        return None
    alert = decode_alert(properties)
    entry_id = _alert_id(subscription["subscription_id"], alert["record"]["record_id"])
    return bool(
        await client.nodes.create_if_absent(
            entry_id, _entry(subscription, alert, now_ms)
        )
    )


async def drain_subscription(
    client: Any,
    subscription: dict[str, Any],
    *,
    consumer: str,
    now_ms: int,
    limit: int = 100,
) -> DrainReport:
    """Move up to ``limit`` queued flips into the owner's inbox."""
    report = DrainReport()
    broker, queue = client.broker, subscription["queue"]
    for _ in range(limit):
        claimed = await broker.consume(
            queue,
            group=CONSUMER_GROUP,
            consumer=consumer,
            now_ms=now_ms,
            lease_ms=LEASE_MS,
        )
        if claimed is None:
            break
        node_id, properties = claimed
        try:
            fresh = await _deliver_one(client, subscription, properties, now_ms)
        except Exception as exc:
            logger.warning("Flip delivery failed (%s); requeued", type(exc).__name__)
            await broker.reject(queue, node_id, requeue=True, now_ms=now_ms)
            report.failed += 1
            break
        await broker.ack(queue, node_id)
        _count(report, fresh)
    return report


def _count(report: DrainReport, fresh: bool | None) -> None:
    if fresh is None:
        report.withheld += 1
    elif fresh:
        report.delivered += 1
    else:
        report.duplicates += 1


async def drain_all(client: Any, *, consumer: str, now_ms: int) -> DrainReport:
    """One delivery pass over every active subscription."""
    total = DrainReport()
    for subscription in await active_subscriptions(client):
        total.add(
            await drain_subscription(
                client, subscription, consumer=consumer, now_ms=now_ms
            )
        )
    return total


async def inbox(client: Any, owner: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """``owner``'s delivered flips, newest first."""
    entries = [
        record
        async for record in labelled(client, ALERT_LABEL)
        if record.get("owner") == owner
    ]
    entries.sort(key=lambda entry: entry.get("delivered_at_ms", 0), reverse=True)
    return entries[:limit]
