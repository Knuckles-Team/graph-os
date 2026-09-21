"""Repository Manager widget — workspace and project status."""

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
    service_type = "repository_manager"
    display_name = "Repository Manager"
    icon = "git-branch"
    category = ServiceCategory.DEVOPS
    description = "Workspace — projects, validation, and build status"
    env_prefix = "REPOSITORY_MANAGER"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("repository_manager")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            repos = client.get_workspace_projects() or []
        except Exception as e:
            logger.warning("Repository Manager fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "projects": len(repos) if isinstance(repos, list) else 0,
                "valid": 0,
                "errors": 0,
            },
            status="ok",
        )
