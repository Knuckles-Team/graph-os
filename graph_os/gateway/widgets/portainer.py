"""Portainer widget — container management dashboard metrics.

Mirrors Homepage's portainer widget (running/stopped/stacks/volumes) but uses
the portainer-agent Python API client directly instead of HTTP proxy.
"""

from __future__ import annotations

import logging
from typing import Any

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)


def _dashboard_metrics(client: Any) -> tuple[int, int, int, int, int]:
    docker_info = client.get_docker_dashboard(environment_id=1)
    if not isinstance(docker_info, dict):
        return 0, 0, 0, 0, 0
    containers = docker_info.get("containers", {})
    images = docker_info.get("images", {})
    return (
        containers.get("running", 0),
        containers.get("stopped", 0),
        docker_info.get("stacks", 0),
        docker_info.get("volumes", 0),
        images.get("total", 0),
    )


def _fallback_container_counts(client: Any) -> tuple[int, int]:
    containers = client.docker_list_containers(environment_id=1, all_containers=True)
    if not isinstance(containers, list):
        return 0, 0
    running = sum(item.get("State", "").lower() == "running" for item in containers)
    return running, len(containers) - running


class Widget(BaseWidget):
    service_type = "portainer"
    display_name = "Portainer"
    icon = "container"
    category = ServiceCategory.INFRASTRUCTURE
    description = "Container management — Docker environments, stacks, and services"
    env_prefix = "PORTAINER"
    supports_websocket = True

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("portainer")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        # Fetch endpoints (environments)
        try:
            endpoints = client.get_endpoints()
            env_count = len(endpoints) if isinstance(endpoints, list) else 0
        except Exception as exc:
            logger.warning("Portainer endpoint discovery failed: %s", exc)
            endpoints = []
            env_count = 0

        running = 0
        stopped = 0
        stacks_count = 0
        volumes_count = 0
        images_count = 0

        # Aggregate across all environments
        try:
            running, stopped, stacks_count, volumes_count, images_count = (
                _dashboard_metrics(client)
            )
        except Exception as e:
            logger.warning("Portainer dashboard fetch: %s", e)
            # Fallback: try listing containers directly
            try:
                running, stopped = _fallback_container_counts(client)
            except Exception as fallback_exc:
                logger.warning("Portainer container fallback failed: %s", fallback_exc)

        return WidgetData(
            fields={
                "running": running,
                "stopped": stopped,
                "stacks": stacks_count,
                "volumes": volumes_count,
                "images": images_count,
                "environments": env_count,
            },
            status="ok",
        )
