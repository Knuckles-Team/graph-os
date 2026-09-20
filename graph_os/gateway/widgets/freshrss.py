"""FreshRSS widget — self-hosted feed reader."""

from __future__ import annotations

import logging

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets._optional_client import import_client
from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)


def _credentials(url: str, username: str, password: str) -> bool:
    return all((url, username, password))


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
        client_cls, missing = import_client("freshrss_agent.api_client", "FreshRSSApi")
        if client_cls is None:
            return WidgetData(status="skipped", error=f"{missing} not installed")

        url = self._resolve_url(config)
        username = self._resolve_env(config, "username")
        api_password = self._resolve_token(config) or self._resolve_env(
            config, "password"
        )
        if not _credentials(url, username, api_password):
            return WidgetData(status="skipped", error="Missing FreshRSS credentials")

        try:
            client = client_cls(
                base_url=url,
                username=username,
                api_password=api_password,
                verify=self._requests_tls_verify(config),
            )
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
