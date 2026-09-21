"""OneTrust widget — privacy/consent platform reachability."""

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
    service_type = "onetrust"
    display_name = "OneTrust"
    icon = "cookie"
    category = ServiceCategory.SECURITY
    description = "Privacy and consent platform — cookie domain inventory"
    env_prefix = "ONETRUST"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="cookie_domains", label="Cookie Domains", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            domains = client.domaindata() or {}
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "cookie_domains": count_items(domains, key="data"),
                "status": "Connected",
            },
            status="ok",
        )
