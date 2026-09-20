"""Characterize the graph-os WebUI host's live contact-delivery path."""

from __future__ import annotations

import importlib
import os
import sys
import types
from types import SimpleNamespace
from typing import Any, cast


def _package(name: str) -> types.ModuleType:
    package = types.ModuleType(name)
    _export(package, "__path__", [])
    return package


def _export(module: types.ModuleType, name: str, value: object) -> None:
    """Populate a synthetic module through its runtime namespace."""

    vars(module)[name] = value


def test_webui_host_import_is_lazy() -> None:
    """The graph-os package remains importable without the optional ag-ui extra."""
    package = importlib.import_module("graph_os.webui_host")
    module = importlib.import_module("graph_os.webui_host.webui_co_service")

    assert package.__all__ == ["run_web_ui"]
    assert package.run_web_ui is module.run_web_ui
    assert module.__all__ == ["run_web_ui"]
    assert callable(module.run_web_ui)


def test_run_web_ui_builds_the_live_contact_delivery_path(
    monkeypatch: Any,
) -> None:
    """Exercise the moved host seam through app construction and shutdown.

    The production imports are intentionally lazy, so this test supplies the
    same public seams that a graph-os installation resolves at runtime. It
    verifies that the moved host still combines AU's context/delegation
    helpers, the WebUI factory's contact-delivery capability, and the
    supervised Uvicorn lifecycle.
    """
    module = importlib.import_module("graph_os.webui_host.webui_co_service")
    calls: dict[str, Any] = {}

    stop_event = __import__("threading").Event()

    class _Config:
        def __init__(self, app: object, *, host: str, port: int, access_log: bool):
            calls["uvicorn_config"] = {
                "app": app,
                "host": host,
                "port": port,
                "access_log": access_log,
            }

    class _Server:
        def __init__(self, config: _Config):
            self.config = config
            self.should_exit = False
            calls["server"] = self

        async def serve(self) -> None:
            calls["served"] = True
            stop_event.set()

    uvicorn = types.ModuleType("uvicorn")
    _export(uvicorn, "Config", _Config)
    _export(uvicorn, "Server", _Server)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

    au = _package("agent_utilities")
    au_core = _package("agent_utilities.core")
    au_server = _package("agent_utilities.server")
    _export(au, "core", au_core)
    _export(au, "server", au_server)
    monkeypatch.setitem(sys.modules, "agent_utilities", au)
    monkeypatch.setitem(sys.modules, "agent_utilities.core", au_core)
    monkeypatch.setitem(sys.modules, "agent_utilities.server", au_server)

    config_module = types.ModuleType("agent_utilities.core.config")
    _export(config_module, "config", SimpleNamespace(host="127.0.0.1", port=8000))
    monkeypatch.setitem(sys.modules, "agent_utilities.core.config", config_module)

    contextual_model = types.ModuleType("agent_utilities.core.contextual_model")

    def create_context_agent(*, model: object) -> object:
        calls["agent_model"] = model
        return "context-agent"

    _export(contextual_model, "create_context_agent", create_context_agent)
    monkeypatch.setitem(
        sys.modules, "agent_utilities.core.contextual_model", contextual_model
    )

    governance = types.ModuleType("agent_utilities.server.webui_contact_governance")

    def contact_delivery_factory_kwargs(
        app_factory: object, sync_runner: object
    ) -> dict[str, object]:
        calls["contact_factory"] = app_factory
        calls["contact_runner"] = sync_runner
        return {"contact_delivery": "governed-contact-delivery"}

    _export(
        governance, "contact_delivery_factory_kwargs", contact_delivery_factory_kwargs
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_utilities.server.webui_contact_governance",
        governance,
    )

    mcp_delegation = types.ModuleType("agent_utilities.server.webui_mcp_delegation")
    _export(
        mcp_delegation,
        "webui_mcp_delegation_helpers",
        lambda: {"call_mcp_tool": "mcp-helper"},
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_utilities.server.webui_mcp_delegation",
        mcp_delegation,
    )

    voice_delegation = types.ModuleType("agent_utilities.server.webui_voice_delegation")
    _export(
        voice_delegation,
        "webui_voice_delegation_helpers",
        lambda: {"transcribe_voice": "voice-helper"},
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_utilities.server.webui_voice_delegation",
        voice_delegation,
    )

    webui = _package("agent_webui")
    webui_api = types.ModuleType("agent_webui.api_extensions")
    _export(webui_api, "_get_engine_bounded", "bounded-engine")
    _export(
        webui_api,
        "_invoke_governed_helper",
        lambda operation, *, deadline: (operation, deadline),
    )
    _export(
        webui, "orchestrator_model", types.ModuleType("agent_webui.orchestrator_model")
    )
    _export(webui, "server", types.ModuleType("agent_webui.server"))
    monkeypatch.setitem(sys.modules, "agent_webui", webui)
    monkeypatch.setitem(sys.modules, "agent_webui.api_extensions", webui_api)

    orchestrator = cast(types.ModuleType, vars(webui)["orchestrator_model"])

    def build_orchestrator_model(engine_getter: object) -> object:
        calls["engine_getter"] = engine_getter
        return "orchestrator-model"

    _export(orchestrator, "build_orchestrator_model", build_orchestrator_model)
    monkeypatch.setitem(sys.modules, "agent_webui.orchestrator_model", orchestrator)

    server_module = cast(types.ModuleType, vars(webui)["server"])

    def create_agent_web_app(
        agent: object,
        *,
        workspace_helpers: dict[str, object],
        listener_host: str,
        contact_delivery: object | None = None,
    ) -> object:
        calls["app"] = {
            "agent": agent,
            "workspace_helpers": workspace_helpers,
            "listener_host": listener_host,
            "contact_delivery": contact_delivery,
        }
        return "webui-app"

    _export(server_module, "create_agent_web_app", create_agent_web_app)
    monkeypatch.setitem(sys.modules, "agent_webui.server", server_module)

    monkeypatch.delenv(module.ACCESS_LOG_POLICY_ENV, raising=False)
    module.run_web_ui(stop_event, host="0.0.0.0", port=8181)

    assert os.environ[module.ACCESS_LOG_POLICY_ENV] == "disabled"
    assert calls["engine_getter"] == "bounded-engine"
    assert calls["agent_model"] == "orchestrator-model"
    assert calls["app"] == {
        "agent": "context-agent",
        "workspace_helpers": {
            "call_mcp_tool": "mcp-helper",
            "transcribe_voice": "voice-helper",
        },
        "listener_host": "0.0.0.0",
        "contact_delivery": "governed-contact-delivery",
    }
    assert calls["contact_factory"] is create_agent_web_app
    assert callable(calls["contact_runner"])
    assert calls["uvicorn_config"] == {
        "app": "webui-app",
        "host": "0.0.0.0",
        "port": 8181,
        "access_log": False,
    }
    assert calls["served"] is True
    assert calls["server"].should_exit is True
