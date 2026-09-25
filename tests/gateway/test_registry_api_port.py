"""The migrated registry projection requires explicit serving authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graph_os.gateway import registry_api


@pytest.fixture(autouse=True)
def clear_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_api, "_registry_ports", None)


def _engine() -> SimpleNamespace:
    registry = SimpleNamespace(
        page=lambda *, limit, cursor: SimpleNamespace(
            entries=[SimpleNamespace(name="local", transport="stdio", desired="enabled")],
            next_cursor=None,
            total_live=1,
        )
    )
    return SimpleNamespace(server_registry=registry, fleet_catalog=None)


def _app() -> FastAPI:
    app = FastAPI()
    registry_api.register_registry_routes(app)
    return app


def test_registry_cannot_mount_without_authority() -> None:
    with pytest.raises(registry_api.CatalogUnavailable):
        _app()


def test_registry_reads_the_injected_eg_catalog_under_verified_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(actor_id="alice", tenant_id="tenant-a", authenticated=True)
    session = SimpleNamespace(actor=actor, tenant="tenant-a")
    monkeypatch.setattr("agent_utilities.api.session.resolve_session", lambda **_: session)
    engine = _engine()
    registry_api.configure_registry_ports(
        registry_api.RegistryPorts(
            catalog_client=lambda tenant, principal: engine,
            grant_digests=lambda _: (),
            toggle_states=lambda _engine, _keys: {},
        )
    )

    response = TestClient(_app()).get("/api/registry/servers")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "kind": "servers",
        "items": [
            {
                "id": "mcp_server_local",
                "name": "local",
                "transport": "stdio",
                "url": "",
                "enabled": True,
            }
        ],
        "count": 1,
        "next_cursor": None,
    }


def test_catalog_port_failure_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(actor_id="alice", tenant_id="tenant-a", authenticated=True)
    session = SimpleNamespace(actor=actor, tenant="tenant-a")
    monkeypatch.setattr("agent_utilities.api.session.resolve_session", lambda **_: session)
    registry_api.configure_registry_ports(
        registry_api.RegistryPorts(
            catalog_client=lambda tenant, principal: (_ for _ in ()).throw(
                registry_api.CatalogUnavailable()
            ),
            grant_digests=lambda _: (),
            toggle_states=lambda _engine, _keys: {},
        )
    )

    response = TestClient(_app()).get("/api/registry/servers")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "catalog_unavailable"}


def test_discovery_requires_current_grant_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(actor_id="alice", tenant_id="tenant-a", authenticated=True)
    session = SimpleNamespace(actor=actor, tenant="tenant-a")
    monkeypatch.setattr("agent_utilities.api.session.resolve_session", lambda **_: session)
    registry_api.configure_registry_ports(
        registry_api.RegistryPorts(
            catalog_client=lambda tenant, principal: _engine(),
            grant_digests=lambda _: (_ for _ in ()).throw(
                registry_api.CatalogUnavailable("grant broker unavailable")
            ),
            toggle_states=lambda _engine, _keys: {},
        )
    )

    response = TestClient(_app()).get("/api/registry/tools")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "catalog_unavailable"}
