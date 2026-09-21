"""Microsoft Clarity widget — web analytics data export."""

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
    service_type = "clarity"
    display_name = "Microsoft Clarity"
    icon = "line-chart"
    category = ServiceCategory.DATA_SCIENCE
    description = "Web analytics — session and traffic data export"
    env_prefix = "CLARITY"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="sessions", label="Sessions", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            export = client.get_data_export(number_of_days=1) or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "sessions": len(export) if isinstance(export, list) else 0,
                "status": "Connected",
            },
            status="ok",
        )
