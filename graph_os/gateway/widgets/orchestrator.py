"""Orchestrator widget — AI agent orchestration hub status."""

from __future__ import annotations

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget


class Widget(BaseWidget):
    service_type = "orchestrator"
    display_name = "Orchestrator"
    icon = "brain"
    category = ServiceCategory.OBSERVABILITY
    description = "Agent orchestration — active agents, skills, and MCP tools"
    env_prefix = "ORCHESTRATOR"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="agents", label="Agents", format="number"),
            WidgetField(key="skills", label="Skills", format="number"),
            WidgetField(key="mcp_tools", label="MCP Tools", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        return WidgetData(
            fields={"agents": 1, "skills": 120, "mcp_tools": 200, "status": "Active"},
            status="ok",
        )
