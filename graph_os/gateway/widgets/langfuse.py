"""Langfuse widget — LLM observability and tracing status."""

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


def _langfuse_summary(posture: object, traces: object) -> tuple[int, str]:
    rows = traces.get("data", []) if isinstance(traces, dict) else []
    total_traces = len(rows) if isinstance(rows, list) else 0
    metadata_only = isinstance(posture, dict) and posture.get("metadata_only") is True
    return total_traces, "OK" if metadata_only else "unknown"


class Widget(BaseWidget):
    service_type = "langfuse"
    display_name = "Langfuse"
    icon = "eye"
    category = ServiceCategory.OBSERVABILITY
    description = "LLM observability — traces, sessions, and scoring"
    env_prefix = "LANGFUSE"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="traces", label="Traces", format="number"),
            WidgetField(key="sessions", label="Sessions", format="number"),
            WidgetField(key="projects", label="Projects", format="number"),
            WidgetField(key="status", label="Status", format="text", highlight=True),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            posture = client.runtime_posture() or {}
            traces = client.trace_list(page=1, limit=1, fields="core") or {}
            total_traces, status_text = _langfuse_summary(posture, traces)
        except Exception as e:  # noqa: BLE001 — status widgets must degrade cleanly
            logger.warning("Langfuse fetch failed (%s).", e)
            return WidgetData(status="error", error="Langfuse request failed")

        return WidgetData(
            fields={
                "traces": total_traces,
                "sessions": 0,
                "projects": 0,
                "status": status_text,
            },
            status="ok" if status_text == "OK" else "unknown",
        )
