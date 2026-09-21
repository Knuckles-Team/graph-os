"""Postiz widget — social media scheduling status."""

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
    service_type = "postiz"
    display_name = "Postiz"
    icon = "share-2"
    category = ServiceCategory.COMMUNICATION
    description = "Social media scheduler — posts, integrations, and analytics"
    env_prefix = "POSTIZ"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="scheduled", label="Scheduled", format="number"),
            WidgetField(key="published", label="Published", format="number"),
            WidgetField(key="integrations", label="Integrations", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            integrations = client.get_integrations() or []
        except Exception as e:
            logger.warning("Postiz fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "scheduled": 0,
                "published": 0,
                "integrations": len(integrations)
                if isinstance(integrations, list)
                else 0,
            },
            status="ok",
        )
