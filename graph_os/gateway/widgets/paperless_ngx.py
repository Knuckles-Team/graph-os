"""Paperless-ngx widget — document management system."""

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
    service_type = "paperless_ngx"
    display_name = "Paperless-ngx"
    icon = "file-text"
    category = ServiceCategory.PRODUCTIVITY
    description = "Document management — pending OCR/consumption tasks"
    env_prefix = "PAPERLESS_NGX"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="pending_tasks", label="Pending Tasks", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            tasks = client.list_tasks(max_pages=1) or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "pending_tasks": count_items(tasks),
                "status": "Connected",
            },
            status="ok",
        )
