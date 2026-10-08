"""GRAPHOS-HOST-R019: GraphOS serves the dashboard REST reads and stream.

agent-webui composes its ``/api`` surface from GraphOS. These tests prove the
routes the frontend calls are mounted, answer with the aggregator's data, and
that ``/ws/dashboard`` accepts a handshake and streams snapshot/update frames.
No live engine or services: the aggregator and daemon are replaced by fakes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway import dashboard_api
from graph_os.gateway.models import DashboardLayout, WidgetData

FRONTEND_PATHS = (
    "/api/dashboard/full",
    "/api/dashboard/health",
    "/api/dashboard/daemon/status",
    "/api/dashboard/daemon/shards",
)


class _FakeAggregator:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def get_layout(self) -> DashboardLayout:
        return DashboardLayout()

    async def fetch_all(self) -> dict[str, WidgetData]:
        return {"alpha": self._widget("alpha"), "beta": self._widget("beta")}

    async def fetch_one(self, service_id: str) -> WidgetData:
        self.fetched.append(service_id)
        return self._widget(service_id)

    @staticmethod
    def _widget(service_id: str) -> WidgetData:
        return WidgetData(fields={"id": service_id}, status="ok")


@pytest.fixture
def aggregator(monkeypatch: pytest.MonkeyPatch) -> _FakeAggregator:
    fake = _FakeAggregator()
    monkeypatch.setattr(dashboard_api, "_aggregator", fake)
    monkeypatch.setattr(dashboard_api, "_WS_PUSH_INTERVAL_SECONDS", 0.05)
    return fake


@pytest.fixture
def client(aggregator: _FakeAggregator) -> TestClient:
    app = FastAPI()
    dashboard_api.register_dashboard_routes(app)
    return TestClient(app)


def test_frontend_dashboard_paths_are_mounted() -> None:
    app = FastAPI()
    dashboard_api.register_dashboard_routes(app)
    paths = set(app.openapi()["paths"])

    assert set(FRONTEND_PATHS) <= paths
    assert any(getattr(route, "path", None) == "/ws/dashboard" for route in app.routes)


def test_registration_is_idempotent_for_the_websocket_route() -> None:
    app = FastAPI()
    dashboard_api.register_dashboard_routes(app)
    dashboard_api.register_dashboard_routes(app)

    ws_routes = [r for r in app.routes if getattr(r, "path", None) == "/ws/dashboard"]
    assert len(ws_routes) == 1


def test_graph_routes_compose_the_dashboard_surface() -> None:
    """register_graph_routes is the composer agent-webui calls."""
    import inspect

    from graph_os.gateway import graph_api

    source = inspect.getsource(graph_api.register_graph_routes)
    assert "register_dashboard_routes(app, prefix=prefix)" in source


def test_full_returns_layout_and_data(client: TestClient) -> None:
    response = client.get("/api/dashboard/full")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"layout", "data"}
    assert set(body["data"]) == {"alpha", "beta"}


def test_daemon_routes_delegate_to_graph_os(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_utilities.knowledge_graph.core import shard_topology

    from graph_os.gateway import daemon

    monkeypatch.setattr(daemon, "daemon_status", lambda: {"role": "host"})
    monkeypatch.setattr(
        shard_topology, "shard_topology_status", lambda: {"mode": "single"}
    )

    assert client.get("/api/dashboard/daemon/status").json() == {"role": "host"}
    assert client.get("/api/dashboard/daemon/shards").json() == {"mode": "single"}


def test_health_is_served_without_capabilities(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_utilities.observability import runtime_health

    async def _report() -> dict[str, Any]:
        return {"status": "ok"}

    monkeypatch.setattr(runtime_health, "collect_health_async", _report)

    response = client.get("/api/dashboard/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_unknown_service_id_shape_is_404(client: TestClient) -> None:
    assert client.get("/api/dashboard/data/..%2Fetc").status_code == 404


def test_websocket_streams_snapshot_then_scoped_update(
    client: TestClient, aggregator: _FakeAggregator
) -> None:
    with client.websocket_connect("/ws/dashboard") as ws:
        snapshot = ws.receive_json()
        ws.send_text('{"type": "subscribe", "widget_ids": ["beta"]}')
        update = ws.receive_json()

    assert snapshot["type"] == "snapshot"
    assert snapshot["sequence"] == 1
    assert set(snapshot["data"]) == {"alpha", "beta"}
    assert update["type"] == "update"
    assert update["stream_id"] == snapshot["stream_id"]
    assert update["sequence"] == 2
    assert set(update["data"]) == {"beta"}
    assert aggregator.fetched == ["beta"]


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", '{"type": "other"}', '{"type": "subscribe", "widget_ids": 1}'],
)
def test_malformed_subscription_is_ignored(raw: str) -> None:
    assert dashboard_api.parsed_widget_subscription(raw) is None


def test_subscription_drops_non_string_ids() -> None:
    raw = '{"type": "subscribe", "widget_ids": ["a", 2, "b"]}'
    assert dashboard_api.parsed_widget_subscription(raw) == {"a", "b"}
