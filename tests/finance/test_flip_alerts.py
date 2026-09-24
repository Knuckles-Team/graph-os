"""EH-416: flips reach durable subscriptions once each and authorise nothing."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from graph_os.finance import delivery, subscriptions, topic
from graph_os.finance.models import FlipFilter, TrackedSeries
from tests.finance.fakes import FinanceClient, Nodes

NOW_MS = 1_790_000_000_000
BTC = TrackedSeries(
    listing_id="binance:BTC/USDT:spot",
    symbol="BTC/USDT",
    asset_class="crypto",
    base="BTC",
    quote="USDT",
    venue="binance",
    interval="1d",
    price_decimals=2,
)
SPY = TrackedSeries(
    listing_id="nyse:SPY",
    symbol="SPY",
    asset_class="etf",
    base="SPY",
    quote="USD",
    venue="nyse",
    interval="1d",
    price_decimals=2,
)


def _record(
    record_id: str, to: str = "bullish", status: str = "emitted"
) -> dict[str, Any]:
    flip = {
        "event_id": f"event-{record_id}",
        "key_digest": "k",
        "from": "bearish" if to == "bullish" else "bullish",
        "to": to,
        "bar_open": 1,
        "effective_at": 2,
        "observed_at": 2,
        "price": 6_500_000,
        "line": 6_400_000,
        "bar_revision": 0,
    }
    return {
        "record_id": record_id,
        "status": status,
        "flip": flip,
        "revises": None,
        "recorded_at": 2,
    }


async def _subscribed(
    client: FinanceClient, owner: str, **match: Any
) -> dict[str, Any]:
    client.consensus.readers.add(owner)
    return await subscriptions.subscribe(
        client, owner, owner, FlipFilter(**match), NOW_MS
    )


async def test_a_flip_is_published_once_and_delivered_once() -> None:
    client = FinanceClient()
    sub = await _subscribed(client, "alice", asset_class="crypto")
    first = await topic.publish_flips(client.broker, BTC, [_record("r1")], NOW_MS)
    again = await topic.publish_flips(client.broker, BTC, [_record("r1")], NOW_MS)
    assert (first.published, again.duplicates) == (1, 1)
    report = await delivery.drain_all(client, consumer="c", now_ms=NOW_MS)
    assert (report.delivered, report.duplicates) == (1, 0)
    [entry] = await delivery.inbox(client, "alice")
    assert (
        entry["record_id"] == "r1"
        and entry["subscription_id"] == sub["subscription_id"]
    )


async def test_a_redelivered_message_lands_in_the_inbox_once() -> None:
    client = FinanceClient()
    sub = await _subscribed(client, "alice")
    await topic.publish_flips(client.broker, BTC, [_record("r1")], NOW_MS)
    queue = sub["queue"]
    claimed = await client.broker.consume(
        queue, group="g", consumer="dead", now_ms=NOW_MS
    )
    assert claimed is not None
    node_id, properties = claimed
    assert await delivery._deliver_one(client, sub, properties, NOW_MS)
    client.broker.expire_claims()  # the consumer died between the write and its ack
    report = await delivery.drain_subscription(client, sub, consumer="c", now_ms=NOW_MS)
    assert (report.delivered, report.duplicates) == (0, 1)
    assert len(await delivery.inbox(client, "alice")) == 1
    assert node_id in client.broker.acked


async def test_filters_route_by_direction_asset_and_listing_and_retractions_arrive() -> (
    None
):
    client = FinanceClient()
    await _subscribed(client, "bull-crypto", asset_class="crypto", direction="bullish")
    await _subscribed(client, "spy", listing_id="nyse:SPY")
    await topic.publish_flips(
        client.broker, BTC, [_record("up"), _record("down", to="bearish")], NOW_MS
    )
    await topic.publish_flips(
        client.broker, SPY, [_record("gone", status="retracted")], NOW_MS
    )
    await delivery.drain_all(client, consumer="c", now_ms=NOW_MS)
    assert [e["record_id"] for e in await delivery.inbox(client, "bull-crypto")] == [
        "up"
    ]
    assert [e["record_id"] for e in await delivery.inbox(client, "spy")] == ["gone"]


async def test_a_failed_write_is_requeued_not_dropped() -> None:
    client = FinanceClient()
    sub = await _subscribed(client, "alice")
    await topic.publish_flips(client.broker, BTC, [_record("r1")], NOW_MS)

    class _Broken(Nodes):
        async def create_if_absent(
            self, node_id: str, properties: dict[str, Any]
        ) -> bool:
            raise ConnectionError("engine went away")

    healthy, client.nodes = client.nodes, _Broken(client.nodes.rows)
    report = await delivery.drain_subscription(client, sub, consumer="c", now_ms=NOW_MS)
    assert report.failed == 1 and client.broker.rejected
    client.nodes = healthy
    retried = await delivery.drain_subscription(
        client, sub, consumer="c", now_ms=NOW_MS
    )
    assert retried.delivered == 1


async def test_only_the_owner_sees_or_cancels_a_subscription() -> None:
    client = FinanceClient()
    sub = await _subscribed(client, "alice")
    assert await subscriptions.active_subscriptions(client, "bob") == []
    with pytest.raises(subscriptions.SubscriptionNotFound):
        await subscriptions.unsubscribe(client, "bob", sub["subscription_id"])
    cancelled = await subscriptions.unsubscribe(client, "alice", sub["subscription_id"])
    assert cancelled["status"] == "cancelled" and client.broker.bindings == []


async def test_an_alert_is_information_not_an_order() -> None:
    client = FinanceClient()
    await _subscribed(client, "alice")
    await topic.publish_flips(client.broker, BTC, [_record("r1")], NOW_MS)
    await delivery.drain_all(client, consumer="c", now_ms=NOW_MS)
    [entry] = await delivery.inbox(client, "alice")
    alert = entry["alert"]
    assert (
        alert["informational_only"] is True
        and "not investment advice" in alert["notice"]
    )
    order_words = {"qty", "side", "order_type", "limit_price", "approval_id", "intent"}
    assert not order_words & (set(alert) | set(alert["record"]) | set(entry))


def test_the_alert_path_cannot_reach_the_order_path() -> None:
    package = Path(__file__).parents[2] / "graph_os" / "finance"
    for name in ("topic", "subscriptions", "delivery", "scheduler", "bars", "store"):
        tree = ast.parse((package / f"{name}.py").read_text(encoding="utf-8"))
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "graph_os.finance.orders" not in imported, name
