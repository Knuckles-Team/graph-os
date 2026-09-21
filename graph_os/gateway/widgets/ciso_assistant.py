"""CISO Assistant widget — GRC platform (assets and risk acceptances)."""

from __future__ import annotations

import logging

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget
from graph_os.gateway.widgets.fleet_client import count_items

logger = logging.getLogger(__name__)


class Widget(BaseWidget):
    service_type = "ciso_assistant"
    display_name = "CISO Assistant"
    icon = "shield-check"
    category = ServiceCategory.SECURITY
    description = "GRC platform — assets and pending risk acceptances"
    env_prefix = "CISO_ASSISTANT"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="assets", label="Assets", format="number"),
            WidgetField(
                key="risk_acceptances",
                label="Risk Acceptances",
                format="number",
                highlight=True,
            ),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            assets = client.api_assets_list() or {}
        except Exception as e:
            return self._error_data(e)

        risk_acceptances = 0
        try:
            acceptances = client.api_risk_acceptances_list() or {}
            risk_acceptances = count_items(acceptances)
        except Exception as e:
            # Best-effort secondary metric: the primary assets call above
            # already proved the service reachable, so a failure here just
            # degrades this one field to 0 rather than the whole widget.
            logger.warning("CISO Assistant risk-acceptance fetch failed: %s", e)

        return WidgetData(
            fields={
                "assets": count_items(assets),
                "risk_acceptances": risk_acceptances,
            },
            status="ok",
        )
