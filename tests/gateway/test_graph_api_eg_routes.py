"""Gateway cutover keeps protocol ingress and mounts only the versioned API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.routing import Match

from graph_os.gateway import graph_api


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    subapp = FastAPI()

    @subapp.get("/registry")
    async def registry() -> dict[str, str]:
        return {"registry_digest": "test-digest"}

    class Application:
        def ensure_tools_registered(self) -> None:
            return None

    monkeypatch.setattr(
        "graph_os.gateway.ports.gateway_application", lambda: Application()
    )
    monkeypatch.setattr(
        "graph_os.mcp_server.runtime.served_api",
        lambda: (SimpleNamespace(services=object()), lambda *_: False),
    )
    monkeypatch.setattr(
        "graph_os.api.http.app.create_api_application", lambda **_: subapp
    )
    monkeypatch.setattr(
        "graph_os.gateway.remote_oauth_api.register_remote_oauth_routes",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("agent_utilities.core.config.config.gateway_rate_limit", 0)
    monkeypatch.setattr("agent_utilities.core.config.config.gateway_metrics", False)
    app = FastAPI()
    monkeypatch.setattr(app, "add_middleware", lambda *_args, **_kwargs: None)
    graph_api.register_graph_routes(app)
    return app


def _has_route(app: FastAPI, path: str, method: str) -> bool:
    scope = {"type": "http", "path": path, "method": method, "root_path": ""}
    return any(route.matches(scope)[0] is Match.FULL for route in app.routes)


def test_only_versioned_operation_routes_are_mounted(gateway: FastAPI) -> None:
    assert _has_route(gateway, "/api/v1/registry", "GET")
    for path in ("/api/sparql", "/api/graph/sql-schema", "/api/graph/query"):
        assert not _has_route(gateway, path, "GET")


def test_fleet_webhook_remains_a_protocol_route(gateway: FastAPI) -> None:
    assert _has_route(gateway, "/fleet/events", "POST")
    assert not _has_route(gateway, "/fleet/events", "GET")
    assert not _has_route(gateway, "/api/fleet/events", "POST")
    routes = [route.path for route in gateway.routes]
    assert routes.count("/fleet/events") == 1
    assert "/fleet/events" not in gateway.openapi()["paths"]


@pytest.mark.parametrize(
    "path",
    (
        "/fleet/health",
        "/fleet/topology",
        "/fleet/pause",
        "/fleet/kill",
        "/fleet/approvals",
        "/fleet/approvals/grant",
        "/fleet/actions/verify",
        "/fleet/trace",
        "/fleet/touched",
    ),
)
def test_retired_fleet_routes_are_not_served(gateway: FastAPI, path: str) -> None:
    assert not _has_route(gateway, path, "GET")
    assert not _has_route(gateway, path, "POST")
