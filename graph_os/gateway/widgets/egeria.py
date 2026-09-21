"""Egeria widget — open metadata and governance platform."""

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
    service_type = "egeria"
    display_name = "Egeria"
    icon = "network"
    category = ServiceCategory.BUSINESS
    description = "Open metadata governance — assets and glossary terms"
    env_prefix = "EGERIA"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="assets", label="Assets", format="number"),
            WidgetField(key="glossary_terms", label="Glossary Terms", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            assets = client.list_assets() or []
        except Exception as e:
            return self._error_data(e)

        glossary_terms = 0
        try:
            glossary_terms = count_items(client.list_glossary_terms() or [])
        except Exception as e:
            # Best-effort secondary metric: list_assets() above already
            # proved the service reachable, so this only degrades one field.
            logger.warning("Egeria glossary-terms fetch failed: %s", e)

        return WidgetData(
            fields={
                "assets": count_items(assets),
                "glossary_terms": glossary_terms,
            },
            status="ok",
        )
