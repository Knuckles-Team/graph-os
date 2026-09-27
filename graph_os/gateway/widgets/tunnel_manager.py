"""Tunnel Manager widget — SSH tunnel and host inventory status."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)


def _host_aliases(response: object) -> Mapping[str, object]:
    """Require the ``tm_hosts(action=list)`` inventory result shape."""
    if not isinstance(response, Mapping):
        raise ValueError("tunnel inventory response has no hosts mapping")
    hosts = response.get("hosts")
    if not isinstance(hosts, Mapping):
        raise ValueError("tunnel inventory response has no hosts mapping")
    return hosts


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
            hosts = _host_aliases(client.list_hosts())
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
