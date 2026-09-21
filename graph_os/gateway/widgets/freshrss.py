"""FreshRSS widget — self-hosted feed reader."""

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


def _subscription_list(subscriptions: object) -> list:
    if isinstance(subscriptions, dict):
        value = subscriptions.get("subscriptions", [])
        return value if isinstance(value, list) else []
    return subscriptions if isinstance(subscriptions, list) else []


def _unread_total(unread_counts: object) -> int:
    if not isinstance(unread_counts, dict):
        return 0
    counts = unread_counts.get("unreadcounts", [])
    return sum(item.get("count", 0) for item in counts if isinstance(item, dict))


class Widget(BaseWidget):
    service_type = "freshrss"
    display_name = "FreshRSS"
    icon = "rss"
    category = ServiceCategory.PRODUCTIVITY
    description = "Feed reader — subscriptions and unread count"
    env_prefix = "FRESHRSS"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="subscriptions", label="Subscriptions", format="number"),
            WidgetField(key="unread", label="Unread", format="number", highlight=True),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            subscriptions = client.subscription_list() or {}
        except Exception as e:
            return self._error_data(e)

        subscription_list = _subscription_list(subscriptions)

        unread = 0
        try:
            unread = _unread_total(client.unread_count() or {})
        except Exception as e:
            # Best-effort secondary metric: subscription_list() above already
            # proved the service reachable, so this only degrades one field.
            logger.warning("FreshRSS unread-count fetch failed: %s", e)

        return WidgetData(
            fields={
                "subscriptions": len(subscription_list),
                "unread": unread,
            },
            status="ok",
        )
