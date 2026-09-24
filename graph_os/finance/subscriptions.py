"""Durable per-owner flip subscriptions (EH-416).

A subscription is a durable EG broker queue bound to ``finance.flip`` with the
owner's filter pattern, plus one ``FinanceFlipSubscription`` node naming its
owner (EG's principal persistence id, and the principal itself so delivery can
re-check its current read authority), filter and queue. graph-os creates both
on its service identity for a caller it has already checked
(:mod:`.authority`); only the owner lists or cancels one. The queue holds every
matching flip until the delivery drain moves it into the owner's inbox, so a
flip published while GraphOS is down is delivered when it comes back.
"""

from __future__ import annotations

import secrets
from typing import Any

from graph_os.finance.models import FlipFilter
from graph_os.finance.store import labelled
from graph_os.finance.topic import FLIP_EXCHANGE, flip_pattern

__all__ = [
    "SUBSCRIPTION_LABEL",
    "SubscriptionNotFound",
    "active_subscriptions",
    "subscribe",
    "unsubscribe",
]

SUBSCRIPTION_LABEL = "FinanceFlipSubscription"
#: A message failing delivery this often is dead-lettered, not retried forever.
MAX_DELIVERY_ATTEMPTS = 5


class SubscriptionNotFound(LookupError):
    """No active subscription with this id belongs to the caller."""


def _node_id(subscription_id: str) -> str:
    return f"finance_flip_subscription:{subscription_id}"


async def subscribe(
    client: Any, owner: str, owner_agent: str, match: FlipFilter, now_ms: int
) -> dict[str, Any]:
    """Create one durable subscription owned by the verified caller."""
    subscription_id = f"flipsub_{secrets.token_hex(8)}"
    queue = f"{FLIP_EXCHANGE}.sub.{subscription_id}"
    pattern = flip_pattern(match)
    broker = client.broker
    await broker.declare_exchange(FLIP_EXCHANGE, "topic")
    await broker.declare_queue(queue, max_delivery_count=MAX_DELIVERY_ATTEMPTS)
    await broker.bind_queue(FLIP_EXCHANGE, queue, pattern)
    record = {
        "type": SUBSCRIPTION_LABEL,
        "subscription_id": subscription_id,
        "owner": owner,
        "owner_agent": owner_agent,
        "filter": match.model_dump(mode="json"),
        "queue": queue,
        "pattern": pattern,
        "status": "active",
        "created_at_ms": now_ms,
    }
    if not await client.nodes.create_if_absent(_node_id(subscription_id), record):
        raise RuntimeError("subscription identifier collision")
    return record


async def _owned(client: Any, owner: str, subscription_id: str) -> dict[str, Any]:
    record = await client.nodes.properties(_node_id(subscription_id))
    if not record or record.get("owner") != owner or record.get("status") != "active":
        raise SubscriptionNotFound(subscription_id)
    return record


async def unsubscribe(client: Any, owner: str, subscription_id: str) -> dict[str, Any]:
    """Cancel one of ``owner``'s subscriptions; its queue stops receiving."""
    record = await _owned(client, owner, subscription_id)
    await client.broker.unbind_queue(FLIP_EXCHANGE, record["queue"], record["pattern"])
    cancelled = await client.nodes.compare_and_set(
        _node_id(subscription_id), {"status": "active"}, {"status": "cancelled"}
    )
    if not cancelled:
        raise SubscriptionNotFound(subscription_id)
    return {**record, "status": "cancelled"}


async def active_subscriptions(
    client: Any, owner: str | None = None
) -> list[dict[str, Any]]:
    """Every active subscription, or only ``owner``'s."""
    return [
        record
        async for record in labelled(client, SUBSCRIPTION_LABEL)
        if record.get("status") == "active" and owner in (None, record.get("owner"))
    ]
