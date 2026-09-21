"""Live wiring and contract tests for the native MCP composition."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.gateway.ports import gateway_application
from graph_os.mcp_server import runtime
from graph_os.mcp_server.composition import (
    NativeGatewayApplication,
    install_gateway_application,
    start_composed_services,
)


def test_native_action_route_contract_matches_cutover_source() -> None:
    from agent_utilities.mcp.kg_server import ACTION_TOOL_ROUTES as source_routes

    graph_os_routes = dict(runtime.ACTION_TOOL_ROUTES)
    browser_route = graph_os_routes.pop("browser_control")
    assert graph_os_routes == source_routes
    assert browser_route == "/browser/control"
    assert len(runtime.ACTION_TOOL_ROUTES) >= 60


@pytest.mark.asyncio
async def test_installed_gateway_port_dispatches_native_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(tool: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((tool, kwargs))
        return {"ok": True}

    monkeypatch.setattr(runtime, "_execute_tool", execute)
    application = install_gateway_application()

    assert gateway_application() is application
    assert await gateway_application().execute_tool("graph_query", query="MATCH") == {
        "ok": True
    }
    assert calls == [("graph_query", {"query": "MATCH"})]


def test_webui_co_service_uses_graph_os_host(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, Any]] = []

    class Supervisor:
        def start_service(self, name: str, runner: Any, session: Any) -> None:
            started.append((name, runner))

    monkeypatch.setattr(
        "graph_os.mcp_server.composition.detect_composition",
        lambda engine, **kwargs: SimpleNamespace(
            messaging_intake_configured=False,
            messaging_configured=False,
            web_ui_enabled=True,
        ),
    )
    monkeypatch.setattr(
        "graph_os.mcp_server.composition.CoServiceSupervisor", Supervisor
    )

    start_composed_services(object(), object())

    assert started[0][0] == "agent-webui"
    assert started[0][1].__module__ == "graph_os.webui_host.webui_co_service"


def test_gateway_adapter_satisfies_runtime_protocol() -> None:
    from graph_os.gateway.ports import GatewayApplicationPort

    assert isinstance(NativeGatewayApplication(), GatewayApplicationPort)


def test_gateway_adapter_uses_native_oauth_binding_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = object()
    expected = (object(),)
    monkeypatch.setattr(
        "graph_os.fleet.multiplexer.current_remote_oauth_grant_bindings",
        lambda value: expected if value is actor else (),
    )

    assert NativeGatewayApplication().remote_oauth_grant_bindings(actor) is expected


def test_console_script_targets_native_serving_entrypoint() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    assert 'graph-os = "graph_os.mcp_server.server:mcp_server"' in pyproject.read_text()
