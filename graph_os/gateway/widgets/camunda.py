"""Camunda widget — process orchestration engine (v7 REST API)."""

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
    service_type = "camunda"
    display_name = "Camunda"
    icon = "workflow"
    category = ServiceCategory.BUSINESS
    description = "Process orchestration — deployed definitions and running instances"
    env_prefix = "CAMUNDA"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(
                key="process_definitions", label="Definitions", format="number"
            ),
            WidgetField(
                key="running_instances",
                label="Running",
                format="number",
                highlight=True,
            ),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            definitions = client.list_process_definitions() or []
            instances = client.list_process_instances() or []
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "process_definitions": len(definitions)
                if isinstance(definitions, list)
                else 0,
                "running_instances": len(instances)
                if isinstance(instances, list)
                else 0,
            },
            status="ok",
        )
