"""Characterize the graph-os WebUI host's live contact-delivery path."""

from __future__ import annotations

import importlib
import os
import sys
import types
from types import SimpleNamespace
from typing import Any, cast

# The host modules the run_web_ui test patches are imported before that test
# replaces agent_utilities / graph_os_webui with synthetic modules: importing them
# afterwards would resolve their own imports against the stubs.
from graph_os.browser_control import browser_control_service
from graph_os.mcp_server import runtime


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

    assert package.__all__ == ["compose_web_application", "run_web_ui"]
    assert package.compose_web_application is module.compose_web_application
    assert package.run_web_ui is module.run_web_ui
    assert module.__all__ == ["compose_web_application", "run_web_ui"]
    assert callable(module.compose_web_application)
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
            assert calls["identity_prepared"] == ("identity-runtime", ["0.0.0.0"])
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

    mcp_delegation = types.ModuleType("graph_os.webui_host.mcp_delegation")
    _export(
        mcp_delegation,
        "webui_mcp_delegation_helpers",
        lambda: {"call_mcp_tool": "mcp-helper"},
    )
    monkeypatch.setitem(
        sys.modules,
        "graph_os.webui_host.mcp_delegation",
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

    webui = _package("graph_os_webui")
    webui_api = types.ModuleType("graph_os_webui.api_extensions")
    _export(webui_api, "get_engine_bounded", "bounded-engine")
    _export(
        webui_api,
        "invoke_governed_helper",
        lambda operation, *, deadline: (operation, deadline),
    )
    _export(
        webui,
        "orchestrator_model",
        types.ModuleType("graph_os_webui.orchestrator_model"),
    )
    _export(webui, "server", types.ModuleType("graph_os_webui.server"))
    browser_control = types.ModuleType("graph_os_webui.browser_control")

    async def revalidate_browser_control_session(_binding: object) -> bool:
        return True

    _export(
        browser_control,
        "revalidate_browser_control_session",
        revalidate_browser_control_session,
    )
    _export(browser_control, "BrowserControlPort", object)
    monkeypatch.setitem(sys.modules, "graph_os_webui", webui)
    monkeypatch.setitem(sys.modules, "graph_os_webui.api_extensions", webui_api)
    monkeypatch.setitem(sys.modules, "graph_os_webui.browser_control", browser_control)

    orchestrator = cast(types.ModuleType, vars(webui)["orchestrator_model"])

    def build_orchestrator_model(engine_getter: object) -> object:
        calls["engine_getter"] = engine_getter
        return "orchestrator-model"

    _export(orchestrator, "build_orchestrator_model", build_orchestrator_model)
    monkeypatch.setitem(sys.modules, "graph_os_webui.orchestrator_model", orchestrator)

    server_module = cast(types.ModuleType, vars(webui)["server"])

    def create_agent_web_app(
        agent: object,
        *,
        workspace_helpers: dict[str, object],
        listener_host: str,
        application_composer: object,
        session_boundary: object | None = None,
        contact_delivery: object | None = None,
        browser_control: object | None = None,
    ) -> object:
        calls["app"] = {
            "agent": agent,
            "workspace_helpers": workspace_helpers,
            "listener_host": listener_host,
            "application_composer": application_composer,
            "session_boundary": session_boundary,
            "contact_delivery": contact_delivery,
            "browser_control": browser_control,
        }
        return "webui-app"

    _export(server_module, "create_agent_web_app", create_agent_web_app)
    monkeypatch.setitem(sys.modules, "graph_os_webui.server", server_module)

    service = object()

    def browser_control_factory_kwargs(
        app_factory: object,
        engine: object,
        sync_runner: object,
        session_revalidator: object,
    ) -> dict[str, object]:
        calls["browser_factory"] = app_factory
        calls["browser_engine"] = engine
        calls["browser_runner"] = sync_runner
        calls["browser_revalidator"] = session_revalidator
        return {"browser_control": service}

    monkeypatch.setattr(
        browser_control_service,
        "browser_control_factory_kwargs",
        browser_control_factory_kwargs,
    )
    monkeypatch.setattr(runtime, "_get_engine", lambda: "graph-os-engine")

    async def prepare_identity(identity: object, bind_hosts: list[str]) -> None:
        calls["identity_prepared"] = (identity, bind_hosts)

    class ServedIdentity:
        async def prepare(self, bind_hosts: list[str]) -> None:
            await prepare_identity("identity-runtime", bind_hosts)

        def webui_session_boundary(self) -> tuple[str, str]:
            return ("boundary", "identity-runtime")

        def console_origin(self) -> str | None:
            return None

    monkeypatch.delenv(module.ACCESS_LOG_POLICY_ENV, raising=False)
    module.run_web_ui(
        stop_event,
        identity_factory=lambda client_for: ServedIdentity(),
        graph_client=runtime.graph_client,
        engine_factory=runtime._get_engine,
        host="0.0.0.0",
        port=8181,
    )

    assert os.environ[module.ACCESS_LOG_POLICY_ENV] == "disabled"
    assert calls["engine_getter"] == "bounded-engine"
    assert calls["agent_model"] == "orchestrator-model"
    composer = calls["app"].pop("application_composer")
    assert callable(composer)
    assert calls["app"] == {
        "agent": "context-agent",
        "workspace_helpers": {
            "call_mcp_tool": "mcp-helper",
            "transcribe_voice": "voice-helper",
        },
        "listener_host": "0.0.0.0",
        "session_boundary": ("boundary", "identity-runtime"),
        "contact_delivery": "governed-contact-delivery",
        "browser_control": service,
    }
    assert calls["contact_factory"] is create_agent_web_app
    assert callable(calls["contact_runner"])
    assert calls["browser_factory"] is create_agent_web_app
    assert calls["browser_engine"] == "graph-os-engine"
    assert callable(calls["browser_runner"])
    assert calls["browser_revalidator"] is revalidate_browser_control_session
    assert calls["uvicorn_config"] == {
        "app": "webui-app",
        "host": "0.0.0.0",
        "port": 8181,
        "access_log": False,
    }
    assert calls["served"] is True
    assert calls["server"].should_exit is True


def test_graph_os_application_composer_mounts_native_routes(monkeypatch: Any) -> None:
    """The injected WebUI seam reaches GraphOS's one REST composition path."""

    module = importlib.import_module("graph_os.webui_host.webui_co_service")
    from graph_os.gateway import graph_api

    app = object()
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        graph_api,
        "register_graph_routes",
        lambda value, prefix="/api": calls.append((value, prefix)),
    )

    module.compose_web_application(app)

    assert calls == [(app, "/api")]
