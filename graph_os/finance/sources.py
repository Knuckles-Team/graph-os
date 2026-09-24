"""Fleet-backed sources the finance surfaces read through the served multiplexer."""

from __future__ import annotations

import json
from typing import Any

from graph_os.finance.models import TrackedSeries
from graph_os.finance.scheduler import FleetBarSource

__all__ = ["FleetNewsSource", "ServedBarSource"]

SEARCH_SERVER = "searxng-mcp"
_MAX_EVIDENCE = 8


class ServedBarSource:
    """:class:`FleetBarSource` for callers not already on the serving loop."""

    async def history(self, series: TrackedSeries, period: str) -> list[dict[str, Any]]:
        from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer

        async def fetch(mux: Any) -> list[dict[str, Any]]:
            return await FleetBarSource(mux).history(series, period)

        return await run_on_served_multiplexer(fetch)


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
        from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer

        direction = alert["record"]["flip"]["to"]
        query = f"{alert['listing_id']} price {direction} trend news"

        async def search(mux: Any) -> Any:
            return await mux.delegate_server_tool(
                server_name=self._server,
                tool_name="web_search",
                arguments={"query": query, "categories": ["news"]},
                timeout=30.0,
            )

        return _evidence(await run_on_served_multiplexer(search))
