"""Atlassian widget — Jira/Confluence integration."""

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
    service_type = "atlassian"
    display_name = "Atlassian"
    icon = "layout-list"
    category = ServiceCategory.PRODUCTIVITY
    description = "Jira & Confluence — issues, sprints, and wiki pages"
    env_prefix = "ATLASSIAN"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("atlassian")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            response = client.jira_cloud_search_for_issues_using_jql(
                jql="assignee = currentUser() AND status != Done", max_results=1
            )
            data = response if isinstance(response, dict) else {}
            total = data.get("total", 0)
        except Exception as e:
            logger.warning("Atlassian fetch failed: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={"open_issues": total, "in_progress": 0, "wiki_pages": 0},
            status="ok",
        )
