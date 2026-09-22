"""Live wiring and contract tests for the native MCP composition."""

from __future__ import annotations

import hashlib
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
from graph_os.mcp_server.server import _register_semantic_content


def test_native_action_route_contract_excludes_legacy_au_surface() -> None:
    assert runtime.ACTION_TOOL_ROUTES == {
        "browser_control": "/browser/control",
        "graph_a2a": "/graph/a2a",
    }


def test_bootstrap_exports_bind_runtime_host_state() -> None:
    """Cycle-breaking bootstrap slots must hold the real runtime authorities."""

    assert (
        runtime.authority_keepalive_scope.__wrapped__.__globals__[
            "_AUTHORITY_KEEPALIVE_ACTIVE"
        ]
        is runtime._AUTHORITY_KEEPALIVE_ACTIVE
    )
    assert (
        runtime._ensure_process_authority_current.__globals__["REGISTERED_TOOLS"]
        is runtime.REGISTERED_TOOLS
    )


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


def test_graphos_runtime_shapes_use_connector_content_contract() -> None:
    from agent_connector_sdk.mcp.content import register_connector_content

    from graph_os.content import connector_content

    class Mcp:
        def __init__(self) -> None:
            self.resources: list[Any] = []

        def add_resource(self, resource: Any) -> None:
            self.resources.append(resource)

        def add_provider(self, provider: Any) -> None:
            raise AssertionError("GraphOS does not package an SDK skill provider")

        def add_prompt(self, prompt: Any) -> None:
            raise AssertionError("GraphOS does not package an SDK prompt")

    mcp = Mcp()
    register_connector_content(mcp, connector_content())

    assert len(mcp.resources) == 1
    resource = mcp.resources[0]
    body = Path(resource.path).read_bytes()
    assert str(resource.uri) == "shapes://graph-os/runtime.shapes.ttl"
    assert resource.name == "shapes://graph-os/runtime.shapes.ttl"
    assert resource.mime_type == "text/turtle"
    assert len(body) == 2757
    assert hashlib.sha256(body).hexdigest() == (
        "e02ad6e9c8cad76af5eb0960bdd47a08346a7ce550532c832cc0b6792bc0aa73"
    )


def test_public_mcp_registers_each_declared_provider_without_relabelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graphos = object()
    au = object()
    calls: list[tuple[object, object]] = []
    mcp = object()
    monkeypatch.setattr(
        "graph_os.mcp_server.server.default_content_providers",
        lambda: (lambda: graphos, lambda: au),
    )
    monkeypatch.setattr(
        "graph_os.mcp_server.server.register_connector_content",
        lambda target, content: calls.append((target, content)),
    )

    _register_semantic_content(mcp)

    assert calls == [(mcp, graphos), (mcp, au)]
