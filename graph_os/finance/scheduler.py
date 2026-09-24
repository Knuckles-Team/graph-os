"""The finance backfill/scan schedule (EH-419).

One tick, per tracked series:

1. **Refresh.** Page the series' bars out of the emerald-exchange connector
   through the served fleet (a full ``period`` backfill when the store holds
   none, a short recent window after that) and append only the versions that
   are new or changed to the EG time-series store.
2. **Scan.** Replay every stored bar version through EG
   ``FinanceMarket.signal_replay`` (the signal is a deterministic projection of
   the bars: EG owns the math, this process holds no signal state), publish its
   flip records on ``finance.flip`` (idempotent on record id) and keep the
   latest ``SignalState`` as a node the scanner reads.

Then one delivery pass moves queued flips into subscribers' inboxes. A failure
on one series is logged and the next series still runs. The schedule runs on
the serving loop under the process authority, like the error-budget throttle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from graph_os.finance.bars import BAR_FIELDS, bar_versions, latest_versions
from graph_os.finance.delivery import drain_all
from graph_os.finance.models import TrackedSeries
from graph_os.finance.store import labelled
from graph_os.finance.topic import publish_flips
from graph_os.mcp_server.background import BackgroundLoopExtension, process_authority

__all__ = [
    "SERIES_LABEL",
    "STATE_LABEL",
    "BarSource",
    "FinanceScheduler",
    "FleetBarSource",
    "attach_finance_scheduler",
    "refresh_series",
    "scan_series",
    "tracked_series",
]

logger = logging.getLogger(__name__)

SERIES_LABEL = "FinanceTrackedSeries"
STATE_LABEL = "FinanceSignalState"
EMERALD_SERVER = "emerald-exchange-mcp"
_PAGE_LIMIT = 5_000
_MAX_PAGES = 200
_SPAN_END = 2**62
_RECENT = {"m": "2d", "h": "14d", "d": "30d", "w": "1y", "M": "2y"}


class BarSource(Protocol):
    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        """Every bar of ``series`` over ``period``, oldest first."""
        ...


def _decoded(result: Any) -> dict[str, Any]:
    body = json.loads(result) if isinstance(result, (str, bytes)) else result
    if not isinstance(body, dict):
        raise ValueError("the connector answered no bar page")
    if "error" in body:
        raise RuntimeError(f"connector refused the history: {body.get('error_type')}")
    return body


class FleetBarSource:
    """Bars from the emerald-exchange connector behind the served multiplexer."""

    def __init__(self, multiplexer: Any, server: str = EMERALD_SERVER) -> None:
        self._mux = multiplexer
        self._server = server

    async def _page(
        self, series: TrackedSeries, period: str, offset: int
    ) -> dict[str, Any]:
        result = await self._mux.delegate_server_tool(
            server_name=self._server,
            tool_name="emerald_market_data",
            arguments={
                "action": "historical",
                "symbol": series.symbol,
                "period": period,
                "interval": series.interval,
                "offset": offset,
                "limit": _PAGE_LIMIT,
            },
            timeout=60.0,
        )
        return _decoded(result)

    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            page = await self._page(series, period, len(bars))
            bars.extend(page.get("bars", []))
            if not page.get("has_more"):
                return bars
        raise RuntimeError("bar history exceeds the page budget; narrow the period")


def _series_node(series: TrackedSeries) -> str:
    return f"finance_tracked_series:{series.series_key}"


def _state_node(series: TrackedSeries) -> str:
    return f"finance_signal_state:{series.series_key}"


def _ts_id(series: TrackedSeries) -> str:
    return f"finance.bars.{series.series_key}"


async def tracked_series(client: Any) -> list[TrackedSeries]:
    """Every series the schedule keeps current."""
    return [
        TrackedSeries.model_validate(record["series"])
        async for record in labelled(client, SERIES_LABEL)
        if record.get("status") == "active"
    ]


async def track(client: Any, series: TrackedSeries, now_ms: int) -> dict[str, Any]:
    """Start (or restart) keeping ``series`` current."""
    record = {
        "type": SERIES_LABEL,
        "series": series.model_dump(mode="json"),
        "status": "active",
        "tracked_at_ms": now_ms,
    }
    await client.nodes.add(_series_node(series), record)
    return record


async def untrack(client: Any, series: TrackedSeries) -> bool:
    """Stop keeping ``series`` current; its stored bars and state remain."""
    return bool(
        await client.nodes.compare_and_set(
            _series_node(series), {"status": "active"}, {"status": "stopped"}
        )
    )


async def _stored_versions(client: Any, series: TrackedSeries) -> list[dict[str, Any]]:
    points = await client.timeseries.range(_ts_id(series), 0, _SPAN_END)
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
            _ts_id(series),
            [(point["ts"], point["values"]) for point in points],
            field_names=BAR_FIELDS,
            bucket_ns=30 * 86_400 * 1_000_000_000,
        )
    return [*stored, *appended]


async def scan_series(
    client: Any, series: TrackedSeries, records: list[dict[str, Any]], now_ns: int
) -> dict[str, Any]:
    """Replay the series, publish its flip records and keep its latest state."""
    replay = await client.finance.market(
        "signal_replay",
        request={
            "series": series.identity(),
            "spec": series.spec(),
            "records": records,
            "as_of": now_ns,
        },
    )
    report = await publish_flips(
        client.broker, series, replay["records"], now_ns // 1_000_000
    )
    await client.nodes.add(
        _state_node(series),
        {
            "type": STATE_LABEL,
            "series_key": series.series_key,
            "listing_id": series.listing_id,
            "asset_class": series.asset_class,
            "state": replay["state"],
            "updated_at_ms": now_ns // 1_000_000,
        },
    )
    return {"published": report.published, "duplicates": report.duplicates}


async def signal_states(client: Any) -> list[dict[str, Any]]:
    """The latest stored ``SignalState`` of every scanned series."""
    return [record async for record in labelled(client, STATE_LABEL)]


def _now_ns() -> int:
    return time.time_ns()


class FinanceScheduler:
    """Refresh, scan and deliver, once per interval."""

    def __init__(
        self,
        *,
        authority: Callable[[], contextlib.AbstractContextManager[Any]],
        source: BarSource,
        consumer: str,
        interval_s: float,
        clock_ns: Callable[[], int] = _now_ns,
    ) -> None:
        self._authority = authority
        self._source = source
        self._consumer = consumer
        self._interval = interval_s
        self._clock_ns = clock_ns

    async def _series_step(
        self, client: Any, series: TrackedSeries, now_ns: int
    ) -> dict[str, Any]:
        try:
            records = await refresh_series(client, self._source, series, now_ns)
            return {
                "listing_id": series.listing_id,
                **await scan_series(client, series, records, now_ns),
            }
        except Exception as exc:
            logger.warning("Finance scan of one series failed (%s)", type(exc).__name__)
            return {"listing_id": series.listing_id, "outcome": "failed"}

    async def tick(self) -> dict[str, Any]:
        """One pass over every tracked series, then one delivery pass."""
        now_ns = self._clock_ns()
        with self._authority() as client:
            reports = [
                await self._series_step(client, series, now_ns)
                for series in await tracked_series(client)
            ]
            drained = await drain_all(
                client, consumer=self._consumer, now_ms=now_ns // 1_000_000
            )
        return {
            "series": reports,
            "delivered": drained.delivered,
            "duplicates": drained.duplicates,
        }

    async def run(self) -> None:
        """Tick once per interval until cancelled; a failed tick is logged."""
        while True:
            try:
                await self.tick()
            except Exception as exc:
                logger.warning("Finance schedule tick failed (%s)", type(exc).__name__)
            await asyncio.sleep(self._interval)


class FinanceSchedulerExtension(BackgroundLoopExtension):
    """Run the finance schedule on the serving loop for the server's lifetime."""

    identifier = "graph-os/finance-schedule"

    def __init__(self, scheduler: FinanceScheduler) -> None:
        super().__init__(scheduler.run)


def attach_finance_scheduler(
    mcp: Any,
    multiplexer: Any,
    session: Any,
    *,
    client_for: Callable[[str], Any],
    interval_s: float,
) -> FinanceScheduler | None:
    """Compose the schedule under the process authority, or ``None`` when off."""
    if interval_s <= 0:
        logger.info("The finance schedule is off (interval %s)", interval_s)
        return None
    scheduler = FinanceScheduler(
        authority=process_authority(session, client_for),
        source=FleetBarSource(multiplexer),
        consumer=f"graph-os:{session.tenant}",
        interval_s=interval_s,
    )
    mcp.add_extension(FinanceSchedulerExtension(scheduler))
    return scheduler
