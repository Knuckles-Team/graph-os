"""The HTTP supervisory operations retain tenant scope after route cutover."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.orchestration.fleet_health import (
    FleetDependencyEvidence,
    FleetHealthEvidence,
    FleetHealthSnapshot,
)

from graph_os.api.ops import fleet as fleet_ops
from graph_os.api.registry import Confirm, Effect, PrincipalRule, Registry, Surface
from graph_os.gateway import fleet as gateway_fleet


def _caller(
    *, tenant: str = "tenant-a", scopes: frozenset[str] = frozenset({"fleet:read"})
) -> Any:
    return SimpleNamespace(
        authenticated=True,
        tenant=tenant,
        effective_scopes=scopes,
        principal_kind="human",
        delegated=False,
    )


def _health_snapshot() -> FleetHealthSnapshot:
    control = FleetDependencyEvidence(
        status="healthy", checked_at=100.0, last_success_at=100.0
    )
    evidence = FleetHealthEvidence(
        status="healthy",
        ready=True,
        autoscaling_ready=True,
        convergence_ready=True,
        generated_at=100.0,
        last_success_at=100.0,
        dependencies={
            "control_store": control,
            "goal_rehydration": control,
            "worker_registry": control,
        },
    )
    return FleetHealthSnapshot(
        evidence=evidence,
        sessions={"total": 1, "by_status": {"running": 1}},
        goals={"active": 99, "tracked": 99},
        domains={
            "tenant-a": {"total": 1, "active": 1, "errored": 0, "error_rate": 0.0}
        },
        dispatch_workers=[{"id": "other-tenant-worker"}],
    )


def test_supervisory_ops_are_http_only_with_exact_read_scope() -> None:
    operations = {op.id: op for op in fleet_ops.operations()}
    for op_id in ("fleet.health", "fleet.topology"):
        op = operations[op_id]
        assert op.scopes == frozenset({"fleet:read"})
        assert op.surfaces == frozenset({Surface.HTTP})
        assert op.binding.handler == "graph_os.api.ops.fleet.handle_fleet_supervision"


def test_containment_ops_require_exact_scope_and_console_confirmation() -> None:
    operations = {op.id: op for op in fleet_ops.operations()}
    for op_id in ("fleet.pause", "fleet.kill"):
        op = operations[op_id]
        assert op.scopes == frozenset({"fleet:control"})
        assert op.effect is Effect.DESTRUCTIVE
        assert op.confirm is Confirm.CONSOLE
        assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
        assert op.surfaces == frozenset({Surface.HTTP, Surface.CONSOLE})
    for params in (
        {},
        {"domain": "x", "session_ids": ["s1"]},
        {"session_ids": ["s1", "s1"]},
    ):
        with pytest.raises(ValueError):
            fleet_ops.FleetContainmentParams.model_validate(params)


def test_http_projection_serves_the_four_operation_paths() -> None:
    from graph_os.api.http.app import create_api_application

    selected = tuple(
        op
        for op in fleet_ops.operations()
        if op.id in {"fleet.health", "fleet.topology", "fleet.pause", "fleet.kill"}
    )
    registry = Registry(selected)
    services = SimpleNamespace(registry=registry)

    async def visible(*_: Any) -> bool:
        return False

    app = create_api_application(services=services, visibility=visible)
    paths = {route.path for route in app.routes}
    assert {f"/ops/{op.id}" for op in selected} <= paths
    assert not any(path.startswith("/fleet/") for path in paths)


def test_fleet_catalog_and_call_share_declarative_resource_routes() -> None:
    from graph_os.api.http.app import create_api_application

    selected = tuple(
        op
        for op in fleet_ops.operations()
        if op.id in {"fleet.catalog.search", "fleet.call"}
    )
    registry = Registry(selected)
    services = SimpleNamespace(registry=registry)

    async def visible(*_: Any) -> bool:
        return False

    app = create_api_application(services=services, visibility=visible)
    methods = {
        route.path: route.methods
        for route in app.routes
        if route.path in {"/fleet/catalog", "/fleet/call"}
    }
    assert methods == {"/fleet/catalog": {"GET"}, "/fleet/call": {"POST"}}


def test_health_read_uses_verified_tenant_for_sql_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, list[Any]]] = []
    snapshot = _health_snapshot()

    def collect(*, scope_resolver: Any) -> Any:
        observed.append(scope_resolver("sqlite"))
        return snapshot

    monkeypatch.setattr(gateway_fleet, "collect_fleet_health", collect)
    payload = gateway_fleet.fleet_health_for_caller(_caller())
    assert payload["sessions"] == snapshot.sessions
    assert payload["domains"] == snapshot.domains
    assert payload["goals"] is None
    assert payload["dispatch_workers"] is None
    assert payload["evidence"]["ready"] is False
    assert (
        payload["evidence"]["dependencies"]["goal_rehydration"]["status"]
        == "unavailable"
    )
    assert "other-tenant-worker" not in str(payload)
    assert observed[0][1] == ["tenant-a"]
    assert "metadata_json" in observed[0][0]


def test_topology_read_reuses_the_same_verified_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[list[Any]] = []
    snapshot = _health_snapshot()

    def collect(*, scope_resolver: Any) -> Any:
        observed.append(scope_resolver("sqlite")[1])
        return snapshot

    def rows(
        current: Any,
        status: str | None,
        limit: int,
        offset: int,
        scope_resolver: Any,
        *,
        allow_control_only: bool,
    ) -> tuple[Any, list[Any]]:
        assert (current, status, limit, offset) == (snapshot, "running", 12, 3)
        assert allow_control_only is True
        observed.append(scope_resolver("postgres")[1])
        return snapshot, []

    monkeypatch.setattr(gateway_fleet, "collect_fleet_health", collect)
    monkeypatch.setattr(gateway_fleet, "_topology_rows", rows)
    payload = gateway_fleet.fleet_topology_for_caller(
        _caller(), status="running", limit=12, offset=3
    )
    assert payload["domains"] == []
    assert payload["goals"] is None
    assert payload["dispatch_workers"] is None
    assert payload["evidence"]["ready"] is False
    assert observed == [["tenant-a"], ["tenant-a"]]


def test_topology_keeps_tenant_page_when_global_goal_registry_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _health_snapshot()
    snapshot = replace(
        base,
        evidence=base.evidence.model_copy(
            update={"status": "partial", "ready": False, "convergence_ready": False}
        ),
    )
    monkeypatch.setattr(gateway_fleet, "collect_fleet_health", lambda **_: snapshot)
    seen: list[list[Any]] = []

    def fetch(
        *, status: str | None, limit: int, offset: int, scope_resolver: Any
    ) -> list[dict[str, Any]]:
        assert (status, limit, offset) == (None, 200, 0)
        seen.append(scope_resolver("sqlite")[1])
        return [
            {
                "id": "own-session",
                "domain": "tenant-a",
                "status": "running",
                "background": False,
                "needs_input": False,
                "updated_at": 100,
            }
        ]

    monkeypatch.setattr(gateway_fleet, "_fetch_sessions", fetch)
    result = gateway_fleet.fleet_topology_for_caller(_caller())
    assert seen == [["tenant-a"]]
    assert result["domains"][0]["sessions"][0]["id"] == "own-session"
    assert result["goals"] is None
    assert result["dispatch_workers"] is None


@pytest.mark.parametrize(
    "caller",
    (
        _caller(tenant=""),
        _caller(scopes=frozenset()),
        SimpleNamespace(
            authenticated=False,
            tenant="tenant-a",
            effective_scopes=frozenset({"fleet:read"}),
        ),
    ),
)
def test_health_refuses_missing_verified_authority(caller: Any) -> None:
    with pytest.raises(PermissionError):
        gateway_fleet.fleet_health_for_caller(caller)


def test_containment_updates_only_verified_tenant_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT, updated_at REAL, metadata_json TEXT)"
    )
    database.executemany(
        "INSERT INTO sessions VALUES (?, 'running', 0, ?)",
        (("own", '{"tenant":"tenant-a"}'), ("other", '{"tenant":"tenant-b"}')),
    )

    class Connection:
        dialect = "sqlite"

        def cursor(self) -> sqlite3.Cursor:
            return database.cursor()

        def commit(self) -> None:
            database.commit()

        def close(self) -> None:
            pass

    cancelled: list[str] = []
    monkeypatch.setattr(gateway_fleet._sessions, "_connect_db", Connection)
    monkeypatch.setattr(gateway_fleet, "_multi_host_state", lambda: False)
    monkeypatch.setattr(
        gateway_fleet,
        "_cancel_session_tasks",
        lambda session_id: cancelled.append(session_id) or False,
    )
    with pytest.raises(PermissionError):
        gateway_fleet.fleet_set_status_for_caller(
            _caller(scopes=frozenset({"fleet:control"})),
            action="kill",
            session_ids=("other",),
        )
    assert cancelled == []
    result = gateway_fleet.fleet_set_status_for_caller(
        _caller(scopes=frozenset({"fleet:control"})),
        action="pause",
        session_ids=("own",),
    )
    assert result["affected"] == ["own"]
    assert cancelled == ["own"]
    assert dict(database.execute("SELECT id, status FROM sessions").fetchall()[0]) == {
        "id": "own",
        "status": "paused",
    }
    assert (
        database.execute("SELECT status FROM sessions WHERE id='other'").fetchone()[0]
        == "running"
    )
    with pytest.raises(PermissionError):
        gateway_fleet._write_fleet_status(
            ["own", "other"],
            "cancelled",
            "kill_requested",
            False,
            scope_resolver=gateway_fleet._caller_tenant_scope(
                _caller(scopes=frozenset({"fleet:control"})),
                required_scope="fleet:control",
            ),
        )
    database.rollback()
    assert cancelled == ["own"], "no task is canceled before every target is fenced"
    database.close()


def test_containment_refuses_service_and_delegated_callers() -> None:
    for changes in ({"principal_kind": "service"}, {"delegated": True}):
        caller = _caller(scopes=frozenset({"fleet:control"}))
        for key, value in changes.items():
            setattr(caller, key, value)
        with pytest.raises(PermissionError):
            gateway_fleet.fleet_set_status_for_caller(
                caller, action="kill", session_ids=("s1",)
            )


def test_topology_handler_passes_only_validated_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Any, dict[str, Any]]] = []

    def topology(caller: Any, **params: Any) -> dict[str, Any]:
        seen.append((caller, params))
        return {"domains": []}

    monkeypatch.setattr(gateway_fleet, "fleet_topology_for_caller", topology)

    async def inline(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(fleet_ops.asyncio, "to_thread", inline)
    op = next(op for op in fleet_ops.operations() if op.id == "fleet.topology")
    params = fleet_ops.FleetTopologyParams(limit=12, offset=3, status="running")
    result = asyncio.run(
        fleet_ops.handle_fleet_supervision(
            SimpleNamespace(caller=_caller()), params.model_dump(), op
        )
    )
    assert result == {"domains": []}
    assert seen == [(_caller(), {"limit": 12, "offset": 3, "status": "running"})]
