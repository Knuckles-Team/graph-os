"""The domain-capability ruling (2026-09-24), tested both ways.

A caller needs the action's exact finance DOMAIN scope AND EG's word that it
could read the tenant graph itself; graph-os then executes on its own service
client, every record is owned by the verified caller, and alert delivery
re-checks the subscriber's read authority at every event.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, get_args

import pytest

from graph_os.finance import delivery, topic
from graph_os.finance.authority import ACTION_SCOPES, FinanceUnavailable
from graph_os.mcp_server.finance import Action, FinanceToolRequest, handle_finance
from tests.finance.fakes import FinanceClient, serving
from tests.finance.test_flip_alerts import BTC, NOW_MS, _record

ALERTS = "finance:alerts"
TRACK = "finance:track"


def _session(principal: str, *scopes: str) -> Any:
    claims = {
        "agent_id": principal,
        "principal": principal,
        "tenant": "tenant-a",
        "delegation": [],
        "scopes": sorted(scopes),
    }
    return SimpleNamespace(
        tenant="tenant-a",
        scopes=frozenset(scopes),
        engine_verified_context=lambda: claims,
    )


async def _ask(principal: str, action: str, *scopes: str, **fields: Any) -> Any:
    request = FinanceToolRequest.model_validate({"action": action, **fields})
    return await handle_finance(_session(principal, *scopes), request)


@pytest.fixture
def service() -> Any:
    client = FinanceClient()
    client.consensus.readers.update({"alice", "bob"})
    with serving(client):
        yield client


def test_every_action_names_exactly_one_domain_scope() -> None:
    actions = set(get_args(Action))
    assert set(ACTION_SCOPES) == actions
    assert set(ACTION_SCOPES.values()) == {
        ALERTS,
        TRACK,
        "finance:backfill",
        "finance:propose-order",
    }


@pytest.mark.parametrize(
    ("action", "wrong"),
    [
        ("subscribe", TRACK),
        ("track", ALERTS),
        ("backfill", TRACK),
        ("propose_order", ALERTS),
        ("subscribe", "compute:finance"),
        ("track", "timeseries:write"),
    ],
)
async def test_a_missing_domain_scope_is_refused_before_any_work(
    service: FinanceClient, action: str, wrong: str
) -> None:
    with pytest.raises(PermissionError, match="FINANCE_SCOPE_REQUIRED"):
        await _ask("alice", action, wrong)
    assert service.consensus.checks == []
    assert service.nodes.rows == {}


async def test_a_caller_without_read_authority_is_refused(
    service: FinanceClient,
) -> None:
    for action, scope in (("subscribe", ALERTS), ("track", TRACK)):
        with pytest.raises(PermissionError, match="FINANCE_READ_AUTHORITY_REQUIRED"):
            await _ask("mallory", action, scope, series=BTC.model_dump())
    assert service.consensus.checks == ["mallory", "mallory"]
    assert service.nodes.rows == {}


async def test_without_the_executor_nothing_runs() -> None:
    with pytest.raises(FinanceUnavailable):
        await _ask("alice", "subscriptions", ALERTS)


async def test_a_subscription_is_owned_and_only_its_owner_cancels_it(
    service: FinanceClient,
) -> None:
    sub = await _ask("alice", "subscribe", ALERTS)
    assert await _ask("bob", "subscriptions", ALERTS) == []
    with pytest.raises(LookupError):
        await _ask("bob", "unsubscribe", ALERTS, subscription_id=sub["subscription_id"])
    [still] = await _ask("alice", "subscriptions", ALERTS)
    assert still["subscription_id"] == sub["subscription_id"]
    await _ask("alice", "unsubscribe", ALERTS, subscription_id=sub["subscription_id"])
    assert await _ask("alice", "subscriptions", ALERTS) == []


async def test_a_track_is_owned_and_only_its_owner_stops_it(
    service: FinanceClient,
) -> None:
    series = BTC.model_dump()
    await _ask("alice", "track", TRACK, series=series)
    assert await _ask("bob", "tracked", TRACK) == []
    stopped = await _ask("bob", "untrack", TRACK, series=series)
    assert stopped == {"stopped": False}
    [mine] = await _ask("alice", "tracked", TRACK)
    assert mine["listing_id"] == BTC.listing_id
    assert await _ask("alice", "untrack", TRACK, series=series) == {"stopped": True}
    assert await _ask("alice", "tracked", TRACK) == []


async def test_revoking_read_authority_mid_stream_stops_delivery(
    service: FinanceClient,
) -> None:
    await _ask("alice", "subscribe", ALERTS)
    await topic.publish_flips(service.broker, BTC, [_record("r1")], NOW_MS)
    first = await delivery.drain_all(service, consumer="c", now_ms=NOW_MS)
    assert (first.delivered, first.withheld) == (1, 0)
    service.consensus.readers.discard("alice")
    await topic.publish_flips(service.broker, BTC, [_record("r2")], NOW_MS)
    second = await delivery.drain_all(service, consumer="c", now_ms=NOW_MS)
    assert (second.delivered, second.withheld) == (0, 1)
    service.consensus.readers.add("alice")
    [entry] = await _ask("alice", "alerts", ALERTS)
    assert entry["record_id"] == "r1", "r2 was never delivered"
    assert await delivery.drain_all(service, consumer="c", now_ms=NOW_MS) == (
        delivery.DrainReport()
    ), "a withheld event is acknowledged, not redelivered on re-grant"
