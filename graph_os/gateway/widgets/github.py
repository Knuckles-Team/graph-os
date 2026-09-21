"""GitHub widget — repository and workflow status."""

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
    service_type = "github"
    display_name = "GitHub"
    icon = "github"
    category = ServiceCategory.DEVOPS
    description = "Repositories — pull requests, issues, and workflow runs"
    env_prefix = "GITHUB"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("github")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            repos = client.list_repos() or []
        except Exception as e:
            logger.warning("GitHub fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "repos": len(repos) if isinstance(repos, list) else 0,
                "open_prs": 0,
                "open_issues": 0,
            },
            status="ok",
        )
