"""Tunnel Manager widget — SSH tunnel and host inventory status."""

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
    service_type = "tunnel_manager"
    display_name = "Tunnel Manager"
    icon = "network"
    category = ServiceCategory.INFRASTRUCTURE
    description = "SSH tunnels — host inventory, active sessions, and connectivity"
    env_prefix = "TUNNEL_MANAGER"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("tunnel_manager")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            hosts = client.list_hosts() or {}
        except Exception as e:
            logger.warning("Tunnel Manager fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "hosts": len(hosts),
                "sessions": 0,
                "status": "Online",
            },
            status="ok",
        )
