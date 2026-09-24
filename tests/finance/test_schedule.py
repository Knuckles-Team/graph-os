"""EH-419: the backfill/scan schedule appends only new bar versions and
publishes each flip record once."""

from __future__ import annotations

import contextlib
from typing import Any

from graph_os.finance import bars, delivery, scheduler, subscriptions
from graph_os.finance.models import FlipFilter, TrackedSeries
from tests.finance.fakes import FinanceClient

DAY_NS = 86_400 * 1_000_000_000
BTC = TrackedSeries(
    listing_id="binance:BTC/USDT:spot",
    symbol="BTC/USDT",
    asset_class="crypto",
    base="BTC",
    quote="USDT",
    venue="binance",
    name="Bitcoin",
    interval="1d",
    price_decimals=2,
    volume_decimals=3,
)
FOMC: list[dict[str, Any]] = [
    {
        "decision_date": "2026-03-18",
        "outcome": "hold",
        "bps_change": 0,
        "target_range_low_pct": 4.25,
        "target_range_high_pct": 4.5,
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260318a.htm",
    },
    {"decision_date": "2026-01-28", "outcome": "unknown", "source_url": "x"},
]


def _bar(day: int, close: float) -> dict[str, Any]:
    return {
        "t": f"2026-09-{day:02d}T00:00:00+00:00",
        "o": close,
        "h": close + 1,
        "l": close - 1,
        "c": close,
        "v": 1.5,
    }


def _open(day: int) -> int:
    return bars.open_time(f"2026-09-{day:02d}T00:00:00+00:00")


def test_bars_become_integer_versions_final_only_after_their_close() -> None:
    fetched = _open(3) + DAY_NS // 2
    [first, second, third] = bars.bar_versions(
        BTC, [_bar(1, 10.25), _bar(2, 11.0), _bar(3, 12.5)], {}, fetched
    )
    assert (first["close"], first["volume"], first["status"]) == (1025, 1500, "final")
    assert second["close_time"] == _open(3) and second["status"] == "final"
    assert third["status"] == "provisional" and third["known_at"] == fetched


def test_only_changed_bars_get_a_new_revision_and_final_never_regresses() -> None:
    fetched = _open(4)
    stored = bars.latest_versions(
        bars.bar_versions(BTC, [_bar(1, 10.0), _bar(2, 11.0)], {}, fetched)
    )
    appended = bars.bar_versions(
        BTC, [_bar(1, 10.0), _bar(2, 11.5)], stored, fetched + 1
    )
    assert [(b["open_time"], b["revision"]) for b in appended] == [(_open(2), 1)]
    early = _open(1) + 1  # an impossible provisional view of a closed bar
    assert bars.bar_versions(BTC, [_bar(1, 9.0)], stored, early) == []


def test_a_monthly_bar_closes_at_the_next_calendar_month() -> None:
    monthly = BTC.model_copy(update={"interval": "1M"})
    opened = bars.open_time("2026-02-01T00:00:00+00:00")
    assert bars.close_time(monthly, opened) == bars.open_time(
        "2026-03-01T00:00:00+00:00"
    )


class _Source:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.periods: list[str] = []
        self.quoted: list[list[str]] = []

    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        self.periods.append(period)
        return self.pages.pop(0)

    async def fomc_decisions(self) -> list[dict[str, Any]]:
        return FOMC

    async def crypto_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        self.quoted.append(symbols)
        return [
            {
                "symbol": "BTC",
                "market_cap_usd": 1.3e12,
                "cmc_rank": 1,
                "last_updated": "t",
            }
        ]


def _replay_flipping_on(record_id: str):
    def replay(request: dict[str, Any]) -> dict[str, Any]:
        assert request["series"] == BTC.identity() and request["spec"] == BTC.spec()
        flips = [
            {
                "record_id": record_id,
                "status": "emitted",
                "flip": {
                    "from": "bearish",
                    "to": "bullish",
                    "bar_open": 0,
                    "effective_at": 1,
                    "price": 1,
                    "line": 1,
                },
                "revises": None,
                "recorded_at": 1,
            }
        ]
        return {
            "state": {"key": {"digest": "k"}, "records": len(request["records"])},
            "records": flips,
            "current": [],
        }

    return replay


async def test_a_tick_backfills_scans_publishes_and_delivers_each_flip_once() -> None:
    client = FinanceClient()
    client.finance.replay = _replay_flipping_on("r1")
    await scheduler.track(client, BTC, 0)
    await subscriptions.subscribe(client, "alice", FlipFilter(), 0)
    source = _Source([[_bar(1, 10.0), _bar(2, 11.0)], [_bar(2, 11.0), _bar(3, 12.0)]])

    @contextlib.contextmanager
    def authority() -> Any:
        yield client

    clock = iter([_open(4), _open(4) + DAY_NS])
    schedule = scheduler.FinanceScheduler(
        authority=authority,
        source=source,
        consumer="c",
        interval_s=60,
        clock_ns=lambda: next(clock),
    )
    first = await schedule.tick()
    second = await schedule.tick()
    assert source.periods == ["1y", "30d"], "backfill once, then a recent window"
    assert len(client.timeseries.points[f"finance.bars.{BTC.series_key}"]) == 3
    assert (first["series"][0]["published"], first["delivered"]) == (1, 1)
    assert (second["series"][0]["duplicates"], second["delivered"]) == (1, 0)
    assert len(await delivery.inbox(client, "alice")) == 1
    [state] = await scheduler.signal_states(client)
    assert state["signalOf"] == BTC.series_node and state["checkpoint"]["records"] == 3
    assert (first["macro_events"], first["market_caps"]) == (1, 1)
    assert source.quoted == [["BTC"], ["BTC"]]


async def test_the_tick_keeps_the_finance_v1_catalog_the_markets_app_reads() -> None:
    client = FinanceClient()
    client.finance.replay = _replay_flipping_on("r1")
    await scheduler.track(client, BTC, 0)

    @contextlib.contextmanager
    def authority() -> Any:
        yield client

    schedule = scheduler.FinanceScheduler(
        authority=authority,
        source=_Source([[_bar(1, 10.0)]]),
        consumer="c",
        interval_s=60,
        clock_ns=lambda: _open(4),
    )
    await schedule.tick()
    rows = client.nodes.rows
    assert rows[BTC.listing_id] == {
        "type": "Listing",
        "listedInstrument": "finance:instrument:BTC",
        "quoteInstrument": "finance:instrument:USDT",
        "listedOn": "finance:venue:binance",
        "listingType": "spot",
        "venueSymbol": "BTC/USDT",
    }
    series = rows[BTC.series_node]
    assert (series["barSeriesOf"], series["barTimeframe"]) == (BTC.listing_id, "1D")
    assert (series["tickSize"], series["volumeStep"]) == ("0.01", "0.001")
    assert series["tsdbSeriesId"] in client.timeseries.points
    base = rows["finance:instrument:BTC"]
    assert (base["assetClass"], base["name"], base["marketCap"]) == (
        "crypto",
        "Bitcoin",
        1.3e12,
    )
    [event] = [r for r in rows.values() if r.get("type") == "MacroEvent"]
    assert (event["policyAction"], event["announcedAt"]) == (
        "hold",
        "2026-03-18T00:00:00Z",
    )
    assert event["sourceUrl"].startswith("https://www.federalreserve.gov/")


async def test_a_failing_series_does_not_stop_the_others() -> None:
    client = FinanceClient()
    client.finance.replay = _replay_flipping_on("r1")
    broken = BTC.model_copy(update={"listing_id": "broken", "symbol": "BRK"})
    await scheduler.track(client, broken, 0)
    await scheduler.track(client, BTC, 0)

    class _Flaky(_Source):
        async def crypto_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
            raise ConnectionError("no CoinMarketCap key")

        async def history(
            self, series: TrackedSeries, period: str
        ) -> list[dict[str, Any]]:
            if series.symbol == "BRK":
                raise ConnectionError("venue down")
            return [_bar(1, 10.0)]

    @contextlib.contextmanager
    def authority() -> Any:
        yield client

    schedule = scheduler.FinanceScheduler(
        authority=authority,
        source=_Flaky([]),
        consumer="c",
        interval_s=60,
        clock_ns=lambda: _open(4),
    )
    report = await schedule.tick()
    outcomes = {row["listing_id"]: row.get("outcome", "ok") for row in report["series"]}
    assert outcomes == {"broken": "failed", BTC.listing_id: "ok"}
    assert (report["macro_events"], report["market_caps"]) == (1, "failed")


async def test_untracked_series_leave_the_schedule() -> None:
    client = FinanceClient()
    await scheduler.track(client, BTC, 0)
    assert await scheduler.untrack(client, BTC)
    assert await scheduler.tracked_series(client) == []


def test_the_schedule_is_off_when_its_interval_is_zero() -> None:
    assert (
        scheduler.attach_finance_scheduler(
            None, None, None, client_for=lambda tenant: None, interval_s=0
        )
        is None
    )
