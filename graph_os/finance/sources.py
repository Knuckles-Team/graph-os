"""The fleet connectors the finance schedule reads, through the served fleet.

* bars -- ``emerald-exchange`` ``emerald_market_data(action="historical")``,
  paged until the connector says there is no more;
* FOMC decisions and crypto market caps -- ``market-data-mcp``
  ``fomc_calendar_list`` and ``cmc_quotes``;
* news around a flip -- ``searxng-mcp`` ``web_search`` (the flip explainer's
  evidence).

GraphOS imports no connector package: every call is an admitted fleet tool on
the served multiplexer, like the dashboard widgets.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

from graph_os.finance.models import TrackedSeries

__all__ = [
    "EMERALD_SERVER",
    "MARKET_DATA_SERVER",
    "FleetMarketSource",
    "FleetNewsSource",
    "ServedMarketSource",
]

EMERALD_SERVER = "emerald-exchange-mcp"
MARKET_DATA_SERVER = "market-data-mcp"
SEARCH_SERVER = "searxng-mcp"
_PAGE_LIMIT = 5_000
_MAX_PAGES = 200
_MAX_EVIDENCE = 8


def _decoded(result: Any) -> dict[str, Any]:
    body = json.loads(result) if isinstance(result, (str, bytes)) else result
    if not isinstance(body, dict):
        raise ValueError("the connector answered no JSON object")
    if "error" in body:
        raise RuntimeError(f"the connector refused: {body.get('error_type', 'error')}")
    return body


class FleetMarketSource:
    """Bars, FOMC decisions and crypto caps from one served multiplexer."""

    def __init__(self, multiplexer: Any) -> None:
        self._mux = multiplexer

    async def _call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._mux.delegate_server_tool(
            server_name=server, tool_name=tool, arguments=arguments, timeout=60.0
        )
        return _decoded(result)

    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        """Every bar of ``series`` over ``period``, oldest first."""
        bars: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            page = await self._call(
                EMERALD_SERVER,
                "emerald_market_data",
                {
                    "action": "historical",
                    "symbol": series.symbol,
                    "period": period,
                    "interval": series.interval,
                    "offset": len(bars),
                    "limit": _PAGE_LIMIT,
                },
            )
            bars.extend(page.get("bars", []))
            if not page.get("has_more"):
                return bars
        raise RuntimeError("bar history exceeds the page budget; narrow the period")

    async def fomc_decisions(self) -> list[dict[str, Any]]:
        """Every sourced FOMC decision record."""
        page = await self._call(
            MARKET_DATA_SERVER, "fomc_calendar_list", {"limit": 1000}
        )
        return list(page.get("items", []))

    async def crypto_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """CoinMarketCap quotes (market cap, rank) for ``symbols``."""
        page = await self._call(
            MARKET_DATA_SERVER, "cmc_quotes", {"symbols": ",".join(symbols)}
        )
        return list(page.get("items", []))


async def _on_served[T](operation: Callable[[Any], Coroutine[Any, Any, T]]) -> T:
    from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer

    return await run_on_served_multiplexer(operation)


class ServedMarketSource:
    """:class:`FleetMarketSource` for callers not already on the serving loop."""

    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        return await _on_served(
            lambda mux: FleetMarketSource(mux).history(series, period)
        )


def _evidence(result: Any) -> list[dict[str, Any]]:
    body = json.loads(result) if isinstance(result, (str, bytes)) else result
    rows = body.get("results", []) if isinstance(body, dict) else []
    return [
        {
            "url": str(row["url"]),
            "title": str(row.get("title", ""))[:300],
            "snippet": str(row.get("content", ""))[:600],
            "published_at": row.get("publishedDate"),
        }
        for row in rows[:_MAX_EVIDENCE]
        if isinstance(row, dict) and str(row.get("url", "")).startswith("https://")
    ]


class FleetNewsSource:
    """News around a flip from the fleet's SearXNG connector (``web_search``)."""

    def __init__(self, server: str = SEARCH_SERVER) -> None:
        self._server = server

    async def around(self, alert: dict[str, Any]) -> list[dict[str, Any]]:
        direction = alert["record"]["flip"]["to"]
        query = f"{alert['listing_id']} price {direction} trend news"

        async def search(mux: Any) -> Any:
            return await mux.delegate_server_tool(
                server_name=self._server,
                tool_name="web_search",
                arguments={"query": query, "categories": ["news"]},
                timeout=30.0,
            )

        return _evidence(await _on_served(search))
