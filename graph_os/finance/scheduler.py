"""The finance backfill/scan schedule (EH-419).

One tick, per tracked series:

1. **Catalog.** Keep the series' finance-v1 listing, instrument, venue and
   bar-series nodes current (:mod:`.catalog`).
2. **Refresh.** Page the series' bars out of the emerald-exchange connector
   through the served fleet (a full ``period`` backfill when the store holds
   none, a short recent window after that) and append only the versions that
   are new or changed to the EG time-series store.
3. **Scan.** Replay every stored bar version through EG
   ``FinanceMarket.signal_replay`` (the signal is a deterministic projection of
   the bars: EG owns the math, this process holds no signal state), publish its
   flip records on ``finance.flip`` (idempotent on record id) and keep the
   latest state as the series' ``SignalState`` checkpoint node.

Then, once per tick: the sourced FOMC decisions as ``MacroEvent`` nodes,
CoinMarketCap caps on the crypto instruments, and one delivery pass moving
queued flips into subscribers' inboxes. A failure on one series or one feed is
logged and the rest still runs. The schedule runs on the serving loop under
the process authority, like the error-budget throttle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from graph_os.finance.authority import FinanceService, install_finance_service
from graph_os.finance.bars import BAR_FIELDS, bar_versions, latest_versions
from graph_os.finance.catalog import (
    STATE_LABEL,
    put_catalog,
    put_macro_events,
    put_market_caps,
    put_signal_state,
)
from graph_os.finance.delivery import drain_all
from graph_os.finance.models import TrackedSeries
from graph_os.finance.sources import FleetMarketSource
from graph_os.finance.store import labelled
from graph_os.finance.topic import publish_flips
from graph_os.mcp_server.background import BackgroundLoopExtension, process_authority

__all__ = [
    "SERIES_LABEL",
    "BarSource",
    "FinanceScheduler",
    "MarketSource",
    "attach_finance",
    "refresh_series",
    "scan_series",
    "signal_states",
    "track",
    "tracked_by",
    "tracked_series",
    "untrack",
]

logger = logging.getLogger(__name__)

SERIES_LABEL = "FinanceTrackedSeries"
_SPAN_END = 2**62
_BUCKET_NS = 30 * 86_400 * 1_000_000_000
_RECENT = {"m": "2d", "h": "14d", "d": "30d", "w": "1y", "M": "2y"}


class BarSource(Protocol):
    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        """Every bar of ``series`` over ``period``, oldest first."""
        ...


class MarketSource(BarSource, Protocol):
    async def fomc_decisions(self) -> list[dict[str, Any]]:
        """Every sourced FOMC decision record."""
        ...

    async def crypto_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """CoinMarketCap quotes for ``symbols``."""
        ...


def _series_node(series: TrackedSeries, owner: str) -> str:
    """One tracking record per (owner, series): each caller tracks its own."""
    return f"finance_tracked_series:{owner.rsplit(':', 1)[-1][:32]}:{series.series_key}"


async def _active_records(client: Any) -> list[dict[str, Any]]:
    return [
        record
        async for record in labelled(client, SERIES_LABEL)
        if record.get("status") == "active"
    ]


async def tracked_series(client: Any) -> list[TrackedSeries]:
    """Every series anyone tracks, once each: what the schedule keeps current."""
    unique: dict[str, TrackedSeries] = {}
    for record in await _active_records(client):
        series = TrackedSeries.model_validate(record["series"])
        unique.setdefault(series.series_key, series)
    return list(unique.values())


async def tracked_by(client: Any, owner: str) -> list[TrackedSeries]:
    """The series ``owner`` tracks."""
    return [
        TrackedSeries.model_validate(record["series"])
        for record in await _active_records(client)
        if record.get("owner") == owner
    ]


async def track(
    client: Any, series: TrackedSeries, owner: str, owner_agent: str, now_ms: int
) -> dict[str, Any]:
    """Start (or restart) tracking ``series`` for ``owner``; writes its catalog now."""
    record = {
        "type": SERIES_LABEL,
        "series": series.model_dump(mode="json"),
        "owner": owner,
        "owner_agent": owner_agent,
        "status": "active",
        "tracked_at_ms": now_ms,
    }
    await client.nodes.add(_series_node(series, owner), record)
    await put_catalog(client, series)
    return record


async def untrack(client: Any, series: TrackedSeries, owner: str) -> bool:
    """Stop ``owner``'s tracking of ``series``; another owner's is untouched."""
    return bool(
        await client.nodes.compare_and_set(
            _series_node(series, owner),
            {"status": "active", "owner": owner},
            {"status": "stopped"},
        )
    )


async def _stored_versions(client: Any, series: TrackedSeries) -> list[dict[str, Any]]:
    points = await client.timeseries.range(series.tsdb_series_id, 0, _SPAN_END)
    if not points:
        return []
    return await client.finance.market(
        "resolve",
        points=[{"ts": ts, "values": values} for ts, values in points],
        finality="include_provisional",
    )


def _refresh_period(series: TrackedSeries, has_history: bool) -> str:
    return _RECENT[series.interval[-1]] if has_history else series.period


async def refresh_series(
    client: Any,
    source: BarSource,
    series: TrackedSeries,
    now_ns: int,
    *,
    backfill: bool = False,
) -> list[dict[str, Any]]:
    """Append new or changed bar versions; returns every stored version."""
    stored = await _stored_versions(client, series)
    period = series.period if backfill else _refresh_period(series, bool(stored))
    bars = await source.history(series, period)
    appended = bar_versions(series, bars, latest_versions(stored), now_ns)
    if appended:
        points = await client.finance.market("encode_points", records=appended)
        await client.timeseries.append(
            series.tsdb_series_id,
            [(point["ts"], point["values"]) for point in points],
            field_names=BAR_FIELDS,
            bucket_ns=_BUCKET_NS,
        )
    return [*stored, *appended]


async def scan_series(
    client: Any, series: TrackedSeries, records: list[dict[str, Any]], now_ns: int
) -> dict[str, Any]:
    """Replay the series, publish its flip records and keep its checkpoint."""
    replay = await client.finance.market(
        "signal_replay",
        request={
            "series": series.identity(),
            "spec": series.spec(),
            "records": records,
            "as_of": now_ns,
        },
    )
    now_ms = now_ns // 1_000_000
    report = await publish_flips(client.broker, series, replay["records"], now_ms)
    await put_signal_state(client, series, replay["state"], now_ms)
    return {"published": report.published, "duplicates": report.duplicates}


async def signal_states(client: Any) -> list[dict[str, Any]]:
    """The ``SignalState`` checkpoint node of every scanned series."""
    return [record async for record in labelled(client, STATE_LABEL)]


def _now_ns() -> int:
    return time.time_ns()


async def _guarded(what: str, step: Awaitable[Any]) -> Any:
    """Run one feed step; a failure is logged and reported, never raised."""
    try:
        return await step
    except Exception as exc:
        logger.warning("Finance schedule step %s failed (%s)", what, type(exc).__name__)
        return "failed"


class FinanceScheduler:
    """Catalog, refresh, scan and deliver, once per interval."""

    def __init__(
        self,
        *,
        authority: Callable[[], contextlib.AbstractContextManager[Any]],
        source: MarketSource,
        consumer: str,
        interval_s: float,
        clock_ns: Callable[[], int] = _now_ns,
    ) -> None:
        self._authority = authority
        self._source = source
        self._consumer = consumer
        self._interval = interval_s
        self._clock_ns = clock_ns

    async def _one_series(
        self, client: Any, series: TrackedSeries, now_ns: int
    ) -> dict[str, Any]:
        await put_catalog(client, series)
        records = await refresh_series(client, self._source, series, now_ns)
        return await scan_series(client, series, records, now_ns)

    async def _series_step(
        self, client: Any, series: TrackedSeries, now_ns: int
    ) -> dict[str, Any]:
        report = await _guarded(
            series.listing_id, self._one_series(client, series, now_ns)
        )
        if report == "failed":
            return {"listing_id": series.listing_id, "outcome": "failed"}
        return {"listing_id": series.listing_id, **report}

    async def _macro(self, client: Any) -> int:
        return await put_macro_events(client, await self._source.fomc_decisions())

    async def _caps(self, client: Any, tracked: list[TrackedSeries]) -> int:
        symbols = sorted({s.base for s in tracked if s.asset_class == "crypto"})
        if not symbols:
            return 0
        quotes = await self._source.crypto_quotes(symbols)
        return await put_market_caps(client, tracked, quotes)

    async def tick(self) -> dict[str, Any]:
        """One pass over every tracked series and feed, then one delivery pass."""
        now_ns = self._clock_ns()
        with self._authority() as client:
            tracked = await tracked_series(client)
            reports = [await self._series_step(client, s, now_ns) for s in tracked]
            macro = await _guarded("fomc", self._macro(client))
            caps = await _guarded("market-caps", self._caps(client, tracked))
            drained = await drain_all(
                client, consumer=self._consumer, now_ms=now_ns // 1_000_000
            )
        return {
            "series": reports,
            "macro_events": macro,
            "market_caps": caps,
            "delivered": drained.delivered,
            "duplicates": drained.duplicates,
        }

    async def run(self) -> None:
        """Tick once per interval until cancelled; a failed tick is logged."""
        while True:
            await _guarded("tick", self.tick())
            await asyncio.sleep(self._interval)


class FinanceSchedulerExtension(BackgroundLoopExtension):
    """Run the finance schedule on the serving loop for the server's lifetime."""

    identifier = "graph-os/finance-schedule"

    def __init__(self, scheduler: FinanceScheduler) -> None:
        super().__init__(scheduler.run)


def attach_finance(
    mcp: Any,
    multiplexer: Any,
    session: Any,
    *,
    client_for: Callable[[str], Any],
    interval_s: float,
) -> FinanceScheduler | None:
    """Compose the finance executor and, unless off, the schedule.

    Both run on the process authority: the executor serves ``graph_finance``
    requests after their caller checks (:mod:`.authority`); the schedule
    returns ``None`` when ``interval_s`` is not positive.
    """
    authority = process_authority(session, client_for)
    install_finance_service(FinanceService(authority))
    if interval_s <= 0:
        logger.info("The finance schedule is off (interval %s)", interval_s)
        return None
    scheduler = FinanceScheduler(
        authority=authority,
        source=FleetMarketSource(multiplexer),
        consumer=f"graph-os:{session.tenant}",
        interval_s=interval_s,
    )
    mcp.add_extension(FinanceSchedulerExtension(scheduler))
    return scheduler
