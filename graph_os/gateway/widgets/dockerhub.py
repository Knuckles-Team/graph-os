"""Docker Hub widget — image registry repository and tag counts."""

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
    service_type = "dockerhub"
    display_name = "Docker Hub"
    icon = "container"
    category = ServiceCategory.DEVOPS
    description = "Image registry — repositories for a namespace"
    env_prefix = "DOCKERHUB"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="repositories", label="Repositories", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        namespace = self._resolve_env(config, "namespace") or self._resolve_env(
            config, "username"
        )
        client = self._fleet_client()

        try:
            repos = client.get_repositories(namespace=namespace) or {}
        except Exception as e:
            return self._error_data(e)

        return WidgetData(
            fields={
                "repositories": count_items(repos),
                "status": "Connected",
            },
            status="ok",
        )
