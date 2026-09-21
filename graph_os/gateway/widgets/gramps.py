"""Gramps widget — genealogy research server."""

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
    service_type = "gramps"
    display_name = "Gramps"
    icon = "trees"
    category = ServiceCategory.LIFESTYLE
    description = "Genealogy research — people and events in the family tree"
    env_prefix = "GRAMPS"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="people", label="People", format="number"),
            WidgetField(key="events", label="Events", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            people = client.get_people() or {}
        except Exception as e:
            return self._error_data(e)

        events = 0
        try:
            events = count_items(client.get_events() or {})
        except Exception as e:
            # Best-effort secondary metric: get_people() above already
            # proved the service reachable, so this only degrades one field.
            logger.warning("Gramps events fetch failed: %s", e)

        return WidgetData(
            fields={
                "people": count_items(people),
                "events": events,
            },
            status="ok",
        )
