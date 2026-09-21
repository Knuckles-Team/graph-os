"""Apache Jena widget — Fuseki triple-store server."""

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
    service_type = "jena"
    display_name = "Apache Jena"
    icon = "database"
    category = ServiceCategory.DATA_SCIENCE
    description = "Fuseki SPARQL triple-store — reachability"
    env_prefix = "JENA"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            client.ping()
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={"status": "Connected"},
            status="ok",
        )
