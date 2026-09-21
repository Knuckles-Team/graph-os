"""ARIS widget — Software AG ARIS process-model repository."""

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
    service_type = "aris"
    display_name = "ARIS"
    icon = "workflow"
    category = ServiceCategory.BUSINESS
    description = "Process model repository — models, functions, and events"
    env_prefix = "ARIS"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="models", label="Models", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            models = client.list_models() or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "models": count_items(models),
                "status": "Connected",
            },
            status="ok",
        )
