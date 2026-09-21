"""Listmonk widget — newsletter and mailing list management."""

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
    service_type = "listmonk"
    display_name = "Listmonk"
    icon = "mail"
    category = ServiceCategory.COMMUNICATION
    description = "Newsletter manager — subscribers, lists, and campaigns"
    env_prefix = "LISTMONK"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="subscribers", label="Subscribers", format="number"),
            WidgetField(key="lists", label="Lists", format="number"),
            WidgetField(key="campaigns", label="Campaigns", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            subscribers = client.get_subscribers(page=1, per_page=1) or {}
            lists = client.get_lists() or []
            campaigns = client.get_campaigns() or []
            total_subs = (
                subscribers.get("total", 0) if isinstance(subscribers, dict) else 0
            )
        except Exception as e:
            logger.warning("Listmonk fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "subscribers": total_subs,
                "lists": len(lists) if isinstance(lists, list) else 0,
                "campaigns": len(campaigns) if isinstance(campaigns, list) else 0,
            },
            status="ok",
        )
