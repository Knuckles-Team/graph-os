"""PulseLink widget — research/news aggregation service."""

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
    service_type = "pulselink"
    display_name = "PulseLink"
    icon = "radio"
    category = ServiceCategory.DATA_SCIENCE
    description = "Research and news aggregation — configured sources"
    env_prefix = "PULSELINK"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="sources", label="Sources", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            sources = client.sources() or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "sources": count_items(sources),
                "status": "Connected",
            },
            status="ok",
        )
