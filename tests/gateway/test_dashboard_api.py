"""The moved dashboard router keeps scoped polling and verified write access."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway import api
from graph_os.gateway.models import DashboardLayout, WidgetData


class _Aggregator:
    def __init__(self) -> None:
        self.fetched: list[str] = []
        self.fetch_all_calls = 0
        self.saved: DashboardLayout | None = None

    def get_layout(self) -> DashboardLayout:
        return DashboardLayout()

    def save_layout(self, layout: DashboardLayout) -> None:
        self.saved = layout

    async def fetch_all(self) -> dict[str, WidgetData]:
        self.fetch_all_calls += 1
        return {"unused": WidgetData()}

    async def fetch_one(self, service_id: str) -> WidgetData:
        self.fetched.append(service_id)
        return WidgetData(fields={"id": service_id})


def test_dashboard_subset_polls_only_requested_widgets_and_write_checks_scope(
    monkeypatch,
) -> None:
    from agent_utilities.api import session

    scopes: list[str] = []
    monkeypatch.setattr(
        session,
        "resolve_session",
        lambda *, required_scope: scopes.append(required_scope),
    )
    aggregator = _Aggregator()
    monkeypatch.setattr(api, "_get_aggregator", lambda: aggregator)
    app = FastAPI()
    api.register_dashboard_routes(app)
    api.register_dashboard_routes(app)
    client = TestClient(app)

    response = client.get("/api/dashboard/data-subset?widget_id=beta&widget_id=alpha")
    assert response.status_code == 200
    assert set(response.json()) == {"alpha", "beta"}
    assert aggregator.fetched == ["alpha", "beta"]
    assert aggregator.fetch_all_calls == 0
    assert scopes == ["kg:read"]
    assert sum(route.path == "/api/dashboard/layout" for route in app.routes) == 2

    response = client.put("/api/dashboard/layout", json=DashboardLayout().model_dump())
    assert response.status_code == 200
    assert aggregator.saved is not None
    assert scopes[-2:] == ["kg:read", "kg:write"]


def test_dashboard_service_id_rejects_invalid_path_before_fetch(monkeypatch) -> None:
    from agent_utilities.api import session

    monkeypatch.setattr(session, "resolve_session", lambda *, required_scope: None)
    aggregator = _Aggregator()
    monkeypatch.setattr(api, "_get_aggregator", lambda: aggregator)
    app = FastAPI()
    api.register_dashboard_routes(app)

    response = TestClient(app).get("/api/dashboard/data/bad%20id")
    assert response.status_code == 404
    assert aggregator.fetched == []


def test_dashboard_rejects_missing_session_and_read_only_layout_write(
    monkeypatch,
) -> None:
    from agent_utilities.api import session

    def missing_session(*, required_scope: str) -> None:
        raise session.SessionRequiredError("missing")

    monkeypatch.setattr(session, "resolve_session", missing_session)
    app = FastAPI()
    api.register_dashboard_routes(app)
    client = TestClient(app)
    assert client.get("/api/dashboard/layout").status_code == 401

    def read_only(*, required_scope: str) -> None:
        if required_scope == "kg:write":
            raise session.ScopeError("denied")

    monkeypatch.setattr(session, "resolve_session", read_only)
    assert client.put("/api/dashboard/layout", json={}).status_code == 403
