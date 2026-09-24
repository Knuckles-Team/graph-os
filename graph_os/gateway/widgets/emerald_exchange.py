"""Emerald Exchange widget — read-only positions and the paper account (EH-423).

Reads the connector's ``emerald_positions_snapshot`` tool through the served
fleet: the venue account's positions and equity (read-only) and the paper
account's positions and unrealized P&L. The widget has no path to place or
cancel an order.
"""

from __future__ import annotations

import json
from typing import Any

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget


def _snapshot(result: Any) -> dict[str, Any]:
    body = json.loads(result) if isinstance(result, (str, bytes)) else result
    if not isinstance(body, dict) or "venue" not in body:
        raise ValueError("the connector answered no positions snapshot")
    return body


class Widget(BaseWidget):
    service_type = "emerald_exchange"
    display_name = "Emerald Exchange"
    icon = "trending-up"
    category = ServiceCategory.BUSINESS
    description = "Trading — read-only venue positions and the paper account"
    env_prefix = "EMERALD_EXCHANGE"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="mode", label="Mode", format="text"),
            WidgetField(key="positions", label="Positions", format="number"),
            WidgetField(key="equity", label="Equity", format="number"),
            WidgetField(
                key="paper_positions", label="Paper positions", format="number"
            ),
            WidgetField(key="paper_pnl", label="Paper P&L", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        try:
            snapshot = _snapshot(self._fleet_client().positions_snapshot())
        except Exception as exc:
            return self._error_data(exc)
        venue, paper = snapshot["venue"], snapshot["paper"]
        return WidgetData(
            fields={
                "mode": snapshot.get("mode", ""),
                "positions": len(venue.get("positions", [])),
                "equity": venue.get("account", {}).get("equity", 0),
                "paper_positions": len(paper.get("positions", [])),
                "paper_pnl": paper.get("unrealized_pnl", 0),
            },
            status="ok",
        )
