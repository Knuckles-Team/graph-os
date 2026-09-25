"""The finance-v1 ABox the schedule keeps current (EH-419).

What the Markets app reads, written from what the schedule tracks and the
fleet reports:

* ``FinancialInstrument`` (``symbol``, ``name``, ``assetClass``; for crypto the
  CoinMarketCap ``marketCap``/``marketCapRank``/``marketCapAsOf``), ``Venue``
  (``name``) and ``Listing`` (``listedInstrument``, ``quoteInstrument``,
  ``listedOn``, ``listingType``, ``venueSymbol``);
* ``BarSeries`` (``barSeriesOf``, ``barTimeframe``, ``tradingCalendar``,
  ``priceBasis``, ``tsdbSeriesId``, ``tickSize``, ``volumeStep``) naming the
  time-series store series the bars are appended to;
* ``SignalState`` per series (``signalOf``, ``trendDirection``, ``dataStatus``
  and the engine's full state as ``checkpoint``), so a scanner reads the latest
  state instead of replaying every series;
* ``MacroEvent`` per sourced FOMC decision (``policyAction``, ``announcedAt``,
  ``sourceUrl``).

Every write is a merge onto a stable node id (create if absent, then a
compare-and-set with no conditions), so re-running a tick changes nothing that
did not change and never drops a property another writer set.
"""

from __future__ import annotations

from typing import Any

from graph_os.finance.models import TrackedSeries, timeframe_label

__all__ = [
    "STATE_LABEL",
    "put_catalog",
    "put_macro_events",
    "put_market_caps",
    "put_signal_state",
    "signal_state_node",
]

STATE_LABEL = "SignalState"
_ACTIONS = {"hike": "hike", "cut": "cut", "hold": "hold"}


async def _merge(client: Any, node_id: str, properties: dict[str, Any]) -> None:
    """Create ``node_id`` or merge ``properties`` into it."""
    if not await client.nodes.create_if_absent(node_id, properties):
        await client.nodes.compare_and_set(node_id, {}, properties)


def _listing(series: TrackedSeries) -> dict[str, Any]:
    return {
        "type": "Listing",
        "listedInstrument": series.instrument_node,
        "quoteInstrument": series.quote_node,
        "listedOn": series.venue_node,
        "listingType": series.listing_type,
        "venueSymbol": series.symbol,
    }


def _bar_series(series: TrackedSeries) -> dict[str, Any]:
    return {
        "type": "BarSeries",
        "barSeriesOf": series.listing_id,
        "barTimeframe": timeframe_label(series.timeframe()),
        "tradingCalendar": series.calendar_id,
        "priceBasis": series.price_basis,
        "tsdbSeriesId": series.tsdb_series_id,
        "tickSize": series.tick_size(),
        "volumeStep": series.volume_step(),
    }


async def put_catalog(client: Any, series: TrackedSeries) -> None:
    """The instrument, quote, venue, listing and bar-series nodes of ``series``."""
    base = {
        "type": "FinancialInstrument",
        "symbol": series.base,
        "name": series.name or series.base,
        "assetClass": series.asset_class,
    }
    await _merge(client, series.instrument_node, base)
    quote = {"type": "FinancialInstrument", "symbol": series.quote}
    await client.nodes.create_if_absent(series.quote_node, quote)
    await _merge(client, series.venue_node, {"type": "Venue", "name": series.venue})
    await _merge(client, series.listing_id, _listing(series))
    await _merge(client, series.series_node, _bar_series(series))


def signal_state_node(series: TrackedSeries) -> str:
    return f"finance:signal:{series.series_key}"


async def put_signal_state(
    client: Any, series: TrackedSeries, state: dict[str, Any], now_ms: int
) -> None:
    """The series' latest signal state, the scanner's checkpoint."""
    await client.nodes.add(
        signal_state_node(series),
        {
            "type": STATE_LABEL,
            "signalOf": series.series_node,
            "listingId": series.listing_id,
            "assetClass": series.asset_class,
            "barTimeframe": timeframe_label(series.timeframe()),
            "trendDirection": state.get("direction") or "none",
            "dataStatus": state.get("data_status", "unavailable"),
            "signalKey": state.get("key", {}).get("digest", ""),
            "checkpoint": state,
            "updatedAtMs": now_ms,
        },
    )


def _macro_event(decision: dict[str, Any]) -> dict[str, Any] | None:
    action = _ACTIONS.get(str(decision.get("outcome")))
    day = str(decision.get("decision_date", ""))
    if action is None or len(day) != 10 or not decision.get("source_url"):
        return None
    return {
        "type": "MacroEvent",
        "name": f"FOMC {day}: {action} ({decision.get('bps_change', 0)} bps)",
        "policyAction": action,
        # The dataset is day-precise; the time is the start of that UTC day.
        "announcedAt": f"{day}T00:00:00Z",
        "announcedPrecision": "day",
        "sourceUrl": decision["source_url"],
        "targetRangeLowPct": decision.get("target_range_low_pct"),
        "targetRangeHighPct": decision.get("target_range_high_pct"),
    }


async def put_macro_events(client: Any, decisions: list[dict[str, Any]]) -> int:
    """One ``MacroEvent`` per sourced FOMC decision; returns how many."""
    written = 0
    for decision in decisions:
        event = _macro_event(decision)
        if event is not None:
            await _merge(
                client, f"finance:macro:fomc:{decision['decision_date']}", event
            )
            written += 1
    return written


async def put_market_caps(
    client: Any, series: list[TrackedSeries], quotes: list[dict[str, Any]]
) -> int:
    """CoinMarketCap caps onto the crypto instruments the schedule tracks."""
    nodes = {
        s.base.upper(): s.instrument_node for s in series if s.asset_class == "crypto"
    }
    written = 0
    for quote in quotes:
        node = nodes.get(str(quote.get("symbol", "")).upper())
        if node is None or quote.get("market_cap_usd") is None:
            continue
        await client.nodes.compare_and_set(
            node,
            {},
            {
                "marketCap": quote["market_cap_usd"],
                "marketCapRank": quote.get("cmc_rank"),
                "marketCapAsOf": quote.get("last_updated"),
                "marketCapSource": "coinmarketcap",
            },
        )
        written += 1
    return written
