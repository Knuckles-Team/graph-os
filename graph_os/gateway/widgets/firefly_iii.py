"""Firefly III widget — personal finance manager."""

from __future__ import annotations

import logging

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)


class Widget(BaseWidget):
    service_type = "firefly_iii"
    display_name = "Firefly III"
    icon = "piggy-bank"
    category = ServiceCategory.LIFESTYLE
    description = "Personal finance manager — accounts and budget summary"
    env_prefix = "FIREFLY_III"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(
                key="summary_entries", label="Summary Entries", format="number"
            ),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            summary = client.get_basic_summary() or {}
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "summary_entries": len(summary) if isinstance(summary, dict) else 0,
                "status": "Connected",
            },
            status="ok",
        )
