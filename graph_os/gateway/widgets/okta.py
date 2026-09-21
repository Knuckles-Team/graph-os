"""Okta widget — identity provider user/group counts."""

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
    service_type = "okta"
    display_name = "Okta"
    icon = "key-round"
    category = ServiceCategory.SECURITY
    description = "Identity provider — managed user count"
    env_prefix = "OKTA"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="users", label="Users", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            users = client.list_users(limit=200, max_items=200) or {}
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "users": count_items(users, key="users"),
                "status": "Connected",
            },
            status="ok",
        )
