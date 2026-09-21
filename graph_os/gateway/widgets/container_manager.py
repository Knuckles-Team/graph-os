"""Container Manager widget — Docker/Podman status via container-manager-mcp."""

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
    service_type = "container_manager"
    display_name = "Container Manager"
    icon = "container"
    category = ServiceCategory.INFRASTRUCTURE
    description = "Docker/Podman — container, image, volume, and network overview"
    env_prefix = "CONTAINER_MANAGER"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("container_manager")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            containers = client.list_containers(all=True) or []
            images = client.list_images() or []
            volumes = client.list_volumes() or []
            networks = client.list_networks() or []
            running = sum(
                1 for c in containers if c.get("status", "").lower() == "running"
            )
        except Exception as e:
            logger.warning("Container Manager fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "containers": len(containers),
                "running": running,
                "images": len(images),
                "volumes": len(volumes) if isinstance(volumes, list) else 0,
                "networks": len(networks),
            },
            status="ok",
        )
