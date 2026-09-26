"""EH-476 runtime routes on the current GraphOS gateway."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from graph_os.gateway import dashboard_runtime as dashboard


def test_runtime_routes_mount_once_with_exact_scope_dependencies() -> None:
    app = FastAPI()
    dashboard.register_dashboard_runtime_routes(app)
    dashboard.register_dashboard_runtime_routes(app)
    routes = {route.path: route for route in app.routes if isinstance(route, APIRoute)}
    assert set(routes) == {
        "/api/dashboard/health",
        "/api/dashboard/daemon/status",
        "/api/dashboard/daemon/shards",
        "/api/dashboard/daemon/start",
        "/api/dashboard/hydrate/{source}",
        "/api/dashboard/hydrate",
        "/api/dashboard/hydration-status",
    }
    assert {
        dep.call for dep in routes["/api/dashboard/hydrate"].dependant.dependencies
    } == {dashboard._require_read, dashboard._require_admin}
    assert {
        dep.call for dep in routes["/api/dashboard/daemon/start"].dependant.dependencies
    } == {dashboard._require_read, dashboard._require_write}
    assert "/api/dashboard/layout" not in routes


def test_runtime_scope_checks_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_utilities.api import session

    requested: list[str] = []
    monkeypatch.setattr(
        session,
        "resolve_session",
        lambda *, required_scope: requested.append(required_scope),
    )
    dashboard._require_read()
    dashboard._require_admin()
    assert requested == ["kg:read", "kg:admin"]

    def denied(*, required_scope: str) -> None:
        raise session.ScopeError(required_scope)

    monkeypatch.setattr(session, "resolve_session", denied)
    with pytest.raises(dashboard.HTTPException) as exc:
        dashboard._require_admin()
    assert exc.value.status_code == 403


async def test_hydration_uses_public_au_port_and_host_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.api import hydration

    from graph_os.gateway import ports

    engine = object()
    calls: list[tuple[str, object, str | None]] = []

    async def direct_call(fn, *args):
        return fn(*args)

    monkeypatch.setattr(dashboard.asyncio, "to_thread", direct_call)
    monkeypatch.setattr(
        ports,
        "gateway_application",
        lambda: type("App", (), {"engine": lambda self: engine})(),
    )
    monkeypatch.setattr(
        hydration,
        "hydrate_source",
        lambda target, source: (
            calls.append(("source", target, source)) or {"source": source}
        ),
    )
    monkeypatch.setattr(
        hydration,
        "hydrate_all",
        lambda target: calls.append(("all", target, None)) or {"status": "queued"},
    )
    monkeypatch.setattr(
        hydration,
        "hydration_status",
        lambda: {"github": {"configured": False}},
    )

    assert await dashboard.trigger_hydration("github") == {"source": "github"}
    assert await dashboard.trigger_all_hydration() == {"status": "queued"}
    assert await dashboard.get_hydration_status() == {"github": {"configured": False}}
    assert calls == [("source", engine, "github"), ("all", engine, None)]
    with pytest.raises(dashboard.HTTPException) as exc:
        await dashboard.trigger_hydration("bad source")
    assert exc.value.status_code == 422
