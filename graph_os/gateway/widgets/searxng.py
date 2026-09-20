"""SearXNG widget — metasearch engine status."""

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
    service_type = "searxng"
    display_name = "SearXNG"
    icon = "search"
    category = ServiceCategory.PRODUCTIVITY
    description = "Metasearch engine — privacy-respecting search aggregator"
    env_prefix = "SEARXNG"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="engines", label="Engines", format="number"),
            WidgetField(key="status", label="Status", format="text", highlight=True),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        url = self._resolve_url(config)
        try:
            with self._http_client(config, timeout=5.0) as client:
                resp = client.get(f"{url}/config")
            data = resp.json() if resp.status_code == 200 else {}
            engines = data.get("engines", [])
        except Exception as e:
            logger.warning("SearXNG fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "engines": len(engines) if isinstance(engines, list) else 0,
                "status": "Online",
            },
            status="ok",
        )
