"""Connector bars into EG bar versions (EH-419).

The emerald-exchange connector answers float OHLCV bars keyed by their ISO
open time. EG's bar contract (``FinanceMarket``) wants integer ticks, a close
time, a final/provisional status, a revision and the time the version became
knowable. This module makes that conversion and decides what to append:
nothing for a bar whose stored latest version already says the same thing, a
new revision for a bar that changed, and never a provisional version over a
final one. Appending is the only write; a stored version is never rewritten.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from typing import Any

from graph_os.finance.models import TrackedSeries

__all__ = ["BAR_FIELDS", "bar_versions", "close_time", "latest_versions", "open_time"]

#: The TSDB field layout of a stored bar point (EG ``codec``).
BAR_FIELDS = [
    "close_time_hi",
    "close_time_lo",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "status",
    "revision",
    "known_at_hi",
    "known_at_lo",
]
_NS = 1_000_000_000
_FIXED_WIDTH_S = {"m": 60, "h": 3_600, "d": 86_400, "w": 7 * 86_400}
_BODY = ("close_time", "open", "high", "low", "close", "volume", "status")


def open_time(stamp: str) -> int:
    """Nanoseconds of an ISO-8601 open time (UTC when unzoned)."""
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp()) * _NS + moment.microsecond * 1_000


def _next_month(opened: datetime) -> datetime:
    days = calendar.monthrange(opened.year, opened.month)[1]
    return opened.replace(day=1) + timedelta(days=days)


def close_time(series: TrackedSeries, opened_ns: int) -> int:
    """The bar's close: open plus the interval (a calendar month for ``1M``)."""
    if series.interval == "1M":
        opened = datetime.fromtimestamp(opened_ns // _NS, tz=UTC)
        return int(_next_month(opened).timestamp()) * _NS
    count, unit = int(series.interval[:-1]), series.interval[-1]
    return opened_ns + count * _FIXED_WIDTH_S[unit] * _NS


def _ticks(value: float, decimals: int) -> int:
    return round(float(value) * 10**decimals)


def _record(
    series: TrackedSeries, bar: dict[str, Any], fetched_ns: int
) -> dict[str, Any]:
    opened = open_time(str(bar["t"]))
    closed = close_time(series, opened)
    final = closed <= fetched_ns
    return {
        "open_time": opened,
        "close_time": closed,
        "open": _ticks(bar["o"], series.price_decimals),
        "high": _ticks(bar["h"], series.price_decimals),
        "low": _ticks(bar["l"], series.price_decimals),
        "close": _ticks(bar["c"], series.price_decimals),
        "volume": _ticks(bar["v"], series.volume_decimals),
        "status": "final" if final else "provisional",
        "revision": 0,
        "known_at": fetched_ns,
    }


def latest_versions(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """The highest revision of every stored bar, by open time."""
    latest: dict[int, dict[str, Any]] = {}
    for record in records:
        held = latest.get(record["open_time"])
        if held is None or record["revision"] > held["revision"]:
            latest[record["open_time"]] = record
    return latest


def _next_version(
    candidate: dict[str, Any], held: dict[str, Any] | None
) -> dict[str, Any] | None:
    if held is None:
        return candidate
    if all(candidate[key] == held[key] for key in _BODY):
        return None
    if held["status"] == "final" and candidate["status"] == "provisional":
        return None
    return {**candidate, "revision": held["revision"] + 1}


def bar_versions(
    series: TrackedSeries,
    bars: list[dict[str, Any]],
    stored: dict[int, dict[str, Any]],
    fetched_ns: int,
) -> list[dict[str, Any]]:
    """The versions to append for ``bars`` given the stored latest versions."""
    appended: list[dict[str, Any]] = []
    for bar in bars:
        candidate = _record(series, bar, fetched_ns)
        version = _next_version(candidate, stored.get(candidate["open_time"]))
        if version is not None:
            appended.append(version)
    return appended
