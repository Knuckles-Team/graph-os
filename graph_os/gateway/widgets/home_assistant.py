"""Home Assistant widget — smart home device and automation status."""

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


def _state_counts(states: list[dict]) -> dict[str, int]:
    return {
        "entities": len(states),
        "lights_on": sum(
            state.get("entity_id", "").startswith("light.")
            and state.get("state") == "on"
            for state in states
        ),
        "automations": sum(
            state.get("entity_id", "").startswith("automation.") for state in states
        ),
        "switches_on": sum(
            state.get("entity_id", "").startswith("switch.")
            and state.get("state") == "on"
            for state in states
        ),
    }


class Widget(BaseWidget):
    service_type = "home_assistant"
    display_name = "Home Assistant"
    icon = "home"
    category = ServiceCategory.LIFESTYLE
    description = "Smart home — devices, automations, and entity states"
    env_prefix = "HOME_ASSISTANT"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("home_assistant")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        from home_assistant_agent.api_client import HomeAssistantApi

        url = self._resolve_url(config)
        token = self._resolve_token(config)
        client = HomeAssistantApi(base_url=url, token=token)

        try:
            states = client.get_states() or []
            fields = _state_counts(states)
        except Exception as e:
            logger.warning("Home Assistant fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields=fields,
            status="ok",
        )
