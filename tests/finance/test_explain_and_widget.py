"""EH-419 explain_flip wiring and the EH-423 read-only positions widget."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.finance import delivery, subscriptions, topic
from graph_os.finance.models import FlipFilter, TrackedSeries
from graph_os.gateway.models import ServiceConfig
from graph_os.gateway.widgets import emerald_exchange
from graph_os.mcp_server import runtime
from graph_os.mcp_server.finance import FinanceToolRequest, handle_finance
from tests.finance.fakes import FinanceClient, principal_ref
from tests.finance.test_flip_alerts import BTC, _record

EVIDENCE = {
    "url": "https://news.example/btc",
    "title": "BTC rallies",
    "snippet": "",
    "published_at": None,
}


async def _inbox_with_flip(client: FinanceClient, owner: str) -> None:
    await subscriptions.subscribe(client, owner, FlipFilter(), 0)
    await topic.publish_flips(client.broker, BTC, [_record("r1")], 0)
    await delivery.drain_all(client, consumer="c", now_ms=0)


def _session(principal: str) -> Any:
    claims = {
        "agent_id": principal,
        "principal": principal,
        "tenant": "tenant-a",
        "delegation": [],
        "scopes": ["kg:read"],
    }
    return SimpleNamespace(tenant="tenant-a", engine_verified_context=lambda: claims)


async def test_explain_flip_reads_the_callers_inbox_gathers_news_and_runs_the_au_explainer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FinanceClient()
    await _inbox_with_flip(client, principal_ref("alice"))
    monkeypatch.setattr(runtime, "graph_client", lambda tenant: client)
    seen: dict[str, Any] = {}

    async def around(self: Any, alert: dict[str, Any]) -> list[dict[str, Any]]:
        seen["alert"] = alert
        return [EVIDENCE]

    async def explain(alert: dict[str, Any], evidence: list[Any]) -> Any:
        seen["evidence"] = evidence
        return SimpleNamespace(
            model_dump=lambda mode: {"math": {"rule": "closed above"}, "claims": []}
        )

    monkeypatch.setattr("graph_os.finance.sources.FleetNewsSource.around", around)
    monkeypatch.setattr("agent_utilities.api.finance.explain_flip", explain)
    request = FinanceToolRequest(action="explain_flip", record_id="r1")
    answer = await handle_finance(_session("alice"), request)
    assert answer["math"]["rule"] == "closed above"
    assert seen["alert"]["record"]["record_id"] == "r1"
    assert [item.url for item in seen["evidence"]] == [EVIDENCE["url"]]
    with pytest.raises(LookupError):
        await handle_finance(_session("mallory"), request)


class _Fleet:
    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot

    def positions_snapshot(self) -> Any:
        return self.snapshot


def test_the_widget_shows_read_only_venue_positions_and_the_paper_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "mode": "paper",
        "venue": {"account": {"equity": 1000.0}, "positions": [{"symbol": "AAPL"}]},
        "paper": {
            "positions": [{"symbol": "AAPL"}, {"symbol": "BTC"}],
            "unrealized_pnl": -2.0,
        },
    }
    widget = emerald_exchange.Widget()
    monkeypatch.setattr(widget, "_fleet_client", lambda: _Fleet(json.dumps(snapshot)))
    data = widget.fetch_data(
        ServiceConfig(id="ee", name="Emerald", widget_type="emerald_exchange")
    )
    assert data.status == "ok"
    assert data.fields == {
        "mode": "paper",
        "positions": 1,
        "equity": 1000.0,
        "paper_positions": 2,
        "paper_pnl": -2.0,
    }
    monkeypatch.setattr(widget, "_fleet_client", lambda: _Fleet({"error": "down"}))
    assert (
        widget.fetch_data(
            ServiceConfig(id="ee", name="Emerald", widget_type="emerald_exchange")
        ).status
        == "error"
    )
    assert not hasattr(_Fleet, "submit_order"), "the widget only reads"


def test_tracked_series_model_maps_intervals_to_eg_timeframes() -> None:
    four_hour = BTC.model_copy(update={"interval": "4h"})
    assert four_hour.timeframe() == {"unit": "hours", "n": 4}
    assert BTC.timeframe() == {"unit": "day"}
    with pytest.raises(ValueError):
        TrackedSeries.model_validate({**BTC.model_dump(), "interval": "3x"})
