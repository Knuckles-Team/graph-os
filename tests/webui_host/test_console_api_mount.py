"""The served WebUI route composer mounts the injected GraphOS API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.identity import composition
from graph_os.webui_host import webui_co_service


def test_explicit_console_origin_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        composition,
        "_setting",
        lambda name: {
            "GRAPHOS_CONSOLE_ORIGIN": "https://console.example.test",
        }.get(name),
    )
    deployment = composition.IdentityDeployment.from_settings(
        SimpleNamespace(deployment_profile="tiny")
    )
    assert deployment.console_origin == "https://console.example.test"
    monkeypatch.setattr(
        composition,
        "_setting",
        lambda name: {
            "GRAPHOS_CONSOLE_ORIGIN": "https://console.example.test/path"
        }.get(name),
    )
    with pytest.raises(ValueError, match="GRAPHOS_CONSOLE_ORIGIN"):
        composition.IdentityDeployment.from_settings(
            SimpleNamespace(deployment_profile="tiny")
        )


def test_served_api_mount_receives_configured_console_origin(monkeypatch) -> None:
    from graph_os.api.http import auth as auth_module
    from graph_os.gateway import graph_api

    monkeypatch.setattr(graph_api, "register_graph_routes", lambda _app: None)
    seen: list[str | None] = []
    actual_auth = auth_module.AmbientHTTPAuthenticator

    class SpyAuth(actual_auth):
        def __init__(self, *, console_origin: str | None = None) -> None:
            seen.append(console_origin)
            super().__init__(console_origin=console_origin)

    monkeypatch.setattr(auth_module, "AmbientHTTPAuthenticator", SpyAuth)

    class EmptyRegistry:
        digest = "digest"

        def __iter__(self):
            return iter(())

    async def visibility(_op, _caller) -> bool:
        return False

    app = FastAPI()
    webui_co_service.compose_web_application(
        app,
        api_services=SimpleNamespace(registry=EmptyRegistry()),
        api_visibility=visibility,
        console_origin="https://console.example.test",
    )
    assert seen == ["https://console.example.test"]
    with TestClient(app) as client:
        response = client.get("/api/v1/registry")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_partial_api_composition_fails_closed(monkeypatch) -> None:
    from graph_os.gateway import graph_api

    monkeypatch.setattr(graph_api, "register_graph_routes", lambda _app: None)
    with pytest.raises(RuntimeError, match="services and visibility"):
        webui_co_service.compose_web_application(FastAPI(), api_services=object())
