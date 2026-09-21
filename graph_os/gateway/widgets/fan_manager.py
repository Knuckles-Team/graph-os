"""Fan Manager widget — local host temperature and fan telemetry.

Unlike the other connector widgets, Fan Manager has no remote REST API: it
wraps local commands (``ipmitool``/``sensors``) on the host the gateway
process itself runs on. No URL/token is required — only the guarded import.
"""

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
    service_type = "fan_manager"
    display_name = "Fan Manager"
    icon = "fan"
    category = ServiceCategory.INFRASTRUCTURE
    description = "Local host temperature and fan-speed telemetry"
    env_prefix = "FAN_MANAGER"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(
                key="core_temp_c", label="Core Temp", format="number", suffix="C"
            ),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            temp = client.get_temp() or {}
        except Exception as e:
            return self._error_data(e)

        if not isinstance(temp, dict) or temp.get("status") != 200:
            return self._error_data(
                RuntimeError(temp.get("error", "sensors read failed"))
                if isinstance(temp, dict)
                else RuntimeError("sensors read failed")
            )

        return WidgetData(
            fields={
                "core_temp_c": temp.get("response", 0),
                "status": "Connected",
            },
            status="ok",
        )
