"""Ansible Tower widget — AWX/Tower automation status."""

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


def _count(values: object) -> int:
    return len(values) if isinstance(values, list) else 0


def _host_count(inventories: object) -> int:
    if not isinstance(inventories, list):
        return 0
    return sum(i.get("total_hosts", 0) for i in inventories if isinstance(i, dict))


class Widget(BaseWidget):
    service_type = "ansible_tower"
    display_name = "Ansible Tower"
    icon = "terminal-square"
    category = ServiceCategory.DEVOPS
    description = "Automation — playbooks, job templates, and inventory management"
    env_prefix = "ANSIBLE_TOWER"

    def get_fields(self) -> list[WidgetField]:
        return self.get_widget_fields("ansible_tower")

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        from ansible_tower_mcp.api_client import Api as AnsibleTowerApi

        url = self._resolve_url(config)
        token = self._resolve_token(config)
        client = AnsibleTowerApi(base_url=url, token=token)
        try:
            templates = client.list_job_templates() or []
            jobs = client.list_jobs(status="running") or []
            failed = client.list_jobs(status="failed") or []
            inventories = client.list_inventories() or []
            hosts = _host_count(inventories)
        except Exception as e:
            logger.warning("Ansible Tower fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "templates": _count(templates),
                "running_jobs": _count(jobs),
                "failed_jobs": _count(failed),
                "hosts": hosts,
            },
            status="ok",
        )
