"""Arr widget — Sonarr/Radarr/Prowlarr media automation suite."""

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
    service_type = "arr"
    display_name = "*Arr Suite"
    icon = "tv"
    category = ServiceCategory.MEDIA
    description = "Media automation — Sonarr, Radarr, Prowlarr, and Lidarr"
    env_prefix = "ARR"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("arr")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            status = client.get_system_status() or {}
        except Exception as e:
            logger.warning("Arr fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={"monitored": 0, "missing": 0, "queued": 0, "indexers": 0},
            status="ok" if status else "unknown",
        )
