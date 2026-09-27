"""The tunnel widget counts the live inventory inside the MCP response."""

from __future__ import annotations

from typing import Any

from graph_os.gateway.models import ServiceConfig
from graph_os.gateway.widgets.tunnel_manager import Widget


def _service() -> ServiceConfig:
    return ServiceConfig(
        id="tunnel-manager", name="Tunnel Manager", widget_type="tunnel_manager"
    )


def test_tunnel_widget_counts_host_aliases_not_response_fields(
    monkeypatch: Any,
) -> None:
    class FleetClient:
        def list_hosts(self) -> dict[str, Any]:
            return {"hosts": {"host-one": {}, "host-two": {}}}

    monkeypatch.setattr(Widget, "_fleet_client", lambda self: FleetClient())

    result = Widget().fetch_data(_service())

    assert result.status == "ok"
    assert result.fields["hosts"] == 2


def test_tunnel_widget_does_not_report_missing_inventory_as_online(
    monkeypatch: Any,
) -> None:
    class FleetClient:
        def list_hosts(self) -> dict[str, Any]:
            return {"status": "unavailable"}

    monkeypatch.setattr(Widget, "_fleet_client", lambda self: FleetClient())

    result = Widget().fetch_data(_service())

    assert result.status == "error"
