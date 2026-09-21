"""HDHomeRun widget — local network TV tuner device."""

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
    service_type = "hdhomerun"
    display_name = "HDHomeRun"
    icon = "tv"
    category = ServiceCategory.MEDIA
    description = "Network TV tuner — channel lineup"
    env_prefix = "HDHOMERUN"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="channels", label="Channels", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            lineup = client.get_lineup() or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "channels": len(lineup) if isinstance(lineup, list) else 0,
                "status": "Connected",
            },
            status="ok",
        )
