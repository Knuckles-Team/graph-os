"""Gateway cutover keeps protocol ingress and mounts only the versioned API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway import graph_api


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    graph_api.register_graph_routes(app)
    # Test route inventory, not authentication middleware behavior.
    app.user_middleware.clear()
    app.middleware_stack = None
    return TestClient(app)


def test_only_versioned_operation_routes_are_mounted(gateway: TestClient) -> None:
    assert gateway.get("/api/v1/registry").json() == {"registry_digest": "test-digest"}
    for path in ("/api/sparql", "/api/graph/sql-schema", "/api/graph/query"):
        assert gateway.get(path).status_code == 404


def test_fleet_webhook_remains_a_protocol_route(gateway: TestClient) -> None:
    assert gateway.get("/fleet/events").status_code == 405
    assert gateway.get("/api/fleet/events").status_code == 404
