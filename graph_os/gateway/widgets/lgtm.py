"""LGTM widget — Loki/Grafana/Tempo/Mimir observability stack."""

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


def _response_list(response: Any) -> list:
    if getattr(response, "status_code", None) != 200:
        return []
    value = response.json()
    return value if isinstance(value, list) else []


class Widget(BaseWidget):
    service_type = "lgtm"
    display_name = "LGTM Stack"
    icon = "activity"
    category = ServiceCategory.OBSERVABILITY
    description = "Observability — Grafana, Loki, Tempo, and Mimir metrics stack"
    env_prefix = "LGTM"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("lgtm")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        url = self._resolve_url(config)
        token = self._resolve_token(config)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with self._http_client(config, timeout=5.0, headers=headers) as client:
                resp = client.get(f"{url}/api/search?type=dash-db")
                dashboards = _response_list(resp)
                alerts_resp = client.get(f"{url}/api/v1/provisioning/alert-rules")
                alerts = _response_list(alerts_resp)
                firing = sum(
                    1
                    for alert in alerts
                    if isinstance(alert, dict) and alert.get("state") == "firing"
                )
                ds_resp = client.get(f"{url}/api/datasources")
                datasources = _response_list(ds_resp)
        except Exception as e:
            logger.warning("LGTM fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "dashboards": len(dashboards),
                "alerts_firing": firing,
                "datasources": len(datasources),
            },
            status="ok",
        )
