"""LeanIX widget — enterprise architecture fact sheets (GraphQL API).

The registry has carried a ``leanix`` widget entry since the dashboard's
inception, but no ``leanix.py`` module ever existed — every discovery cycle
silently no-op'd on the missing module. This file closes that gap.
"""

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

_FACT_SHEET_COUNT_QUERY = "{ allFactSheets { totalCount } }"


class Widget(BaseWidget):
    service_type = "leanix"
    display_name = "LeanIX"
    icon = "layout-grid"
    category = ServiceCategory.BUSINESS
    description = "Enterprise architecture — fact sheet inventory"
    env_prefix = "LEANIX"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="fact_sheets", label="Fact Sheets", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            result = client.query(query=_FACT_SHEET_COUNT_QUERY) or {}
        except Exception as e:
            return self._error_data(e)

        fact_sheets = 0
        if isinstance(result, dict):
            fact_sheets = (
                result.get("data", {}).get("allFactSheets", {}).get("totalCount", 0)
            )

        return WidgetData(
            fields={
                "fact_sheets": fact_sheets,
                "status": "Connected",
            },
            status="ok",
        )
