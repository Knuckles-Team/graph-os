"""Tests for graph_os.gateway.registry — migrated from service-dashboard-core."""

import asyncio

from graph_os.gateway.aggregator import Aggregator
from graph_os.gateway.models import ServiceConfig
from graph_os.gateway.registry import Registry, get_registry


def test_registry_singleton():
    reg1 = get_registry()
    reg2 = get_registry()
    assert reg1 is reg2


def test_list_all_known():
    reg = Registry()
    known = reg.list_all_known()
    assert "portainer" in known
    assert "uptime_kuma" in known
    assert "technitium" in known


def test_get_invalid_widget():
    reg = Registry()
    widget = reg.get_widget("non_existent_widget")
    assert widget is None


def test_retired_static_orchestrator_widget_reports_unavailable() -> None:
    registry = Registry()
    assert "orchestrator" not in registry.list_all_known()
    aggregator = Aggregator(registry=registry, config_manager=object())
    try:
        result = asyncio.run(
            aggregator._fetch_one(
                ServiceConfig(id="agent", name="Agent", widget_type="orchestrator")
            )
        )
    finally:
        aggregator._executor.shutdown(wait=True)
    assert result.status == "error"
    assert result.error == "widget type is unavailable"
    assert result.fields == {}
