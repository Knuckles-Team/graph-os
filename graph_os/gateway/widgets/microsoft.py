"""Microsoft widget — Microsoft 365 integration status."""

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
    service_type = "microsoft"
    display_name = "Microsoft 365"
    icon = "layout-grid"
    category = ServiceCategory.PRODUCTIVITY
    description = "Microsoft 365 — Teams, Outlook, OneDrive integration"
    env_prefix = "MICROSOFT"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("microsoft")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            mail = client.get_unread_count() or 0
            events = client.get_today_events() or []
        except Exception as e:
            logger.warning("Microsoft fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "unread_emails": mail if isinstance(mail, int) else 0,
                "events_today": len(events) if isinstance(events, list) else 0,
                "status": "Connected",
            },
            status="ok",
        )
