"""OpenBao widget — secrets engine and vault status."""

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
    service_type = "openbao"
    display_name = "OpenBao"
    icon = "lock"
    category = ServiceCategory.SECURITY
    description = "Vault — secrets engines, seal status, and health"
    env_prefix = "OPENBAO"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("openbao")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            health = client.get_health() or {}
            mounts = client.get_mounts() or {}
            sealed = health.get("sealed", True)
            version = health.get("version", "unknown")
            mount_count = len(mounts) if isinstance(mounts, dict) else 0
        except Exception as e:
            logger.warning("OpenBao fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "sealed": "Yes" if sealed else "No",
                "mounts": mount_count,
                "version": version,
            },
            status="ok" if not sealed else "error",
        )
