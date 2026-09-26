"""The SDK pack publisher must never receive a fabricated catalog binding."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agent_utilities.knowledge_graph.core.fleet_catalog_tables import (
    TenantLocalDiscoveryBinding,
)

from graph_os.fleet.catalog_snapshot import (
    CatalogSnapshotUnavailable,
    ChildCatalogSnapshot,
    registration_config_digest,
)
from graph_os.fleet.multiplexer import MCPMultiplexer


@pytest.mark.asyncio
async def test_pack_resolver_requires_installation_and_current_mounted_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mux = MCPMultiplexer(SimpleNamespace())
    current = True
    checks: list[str] = []

    def current_child(server_name: str) -> object:
        checks.append(server_name)
        if not current:
            raise CatalogSnapshotUnavailable("mounted child changed")
        return object()

    monkeypatch.setattr(mux, "child_catalog_snapshot", current_child)
    assert not mux.connector_pack_authority_ready()
    with pytest.raises(CatalogSnapshotUnavailable, match="authority"):
        await mux.resolve_connector_pack_authority("source-mcp")

    async def resolve(server_name: str) -> object:
        nonlocal current
        current = False
        return server_name

    mux.install_connector_pack_authority(resolve)
    assert mux.connector_pack_authority_ready()
    with pytest.raises(CatalogSnapshotUnavailable, match="changed"):
        await mux.resolve_connector_pack_authority("source-mcp")
    assert checks == ["source-mcp", "source-mcp"]
    with pytest.raises(CatalogSnapshotUnavailable, match="authority"):
        mux.install_connector_pack_authority(resolve)


def _observation(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "server_name": "source-mcp",
        "component_id": "mcp:source-mcp/mcp_server/source-mcp",
        "registry_revision": 12,
        "registry_digest": "sha256:" + "a" * 64,
        "registration_config_digest": registration_config_digest(
            "http://child-a", (("source", "tenant-a"),)
        ),
        "component_revision": 8,
        "component_digest": "sha256:" + "b" * 64,
        "discovery_tenant": "tenant-a",
        "local_catalog_epoch": 3,
        "child_id": "mounted-child-a",
        "child_connection_generation": 2,
        "tools": [{"name": "z"}, {"name": "a"}],
        "resources": [{"uri": "b"}, {"uri": "a"}],
        "resource_templates": [{"uriTemplate": "x/{id}"}],
        "prompts": [{"name": "explain"}],
        "family_errors": {"resource_templates": "unsupported"},
    }
    values.update(overrides)
    return values


def test_four_family_observation_is_immutable_and_order_independent() -> None:
    source = _observation()
    first = ChildCatalogSnapshot.from_observation(**source)
    source["tools"].reverse()
    second = ChildCatalogSnapshot.from_observation(**source)

    assert first == second
    assert json.loads(first.canonical_json)["unsupported_families"] == [
        "resource_templates"
    ]
    assert first.registry_revision == 12


def test_registration_config_digest_tracks_served_fields_only() -> None:
    first = registration_config_digest("http://child-a", (("z", "1"), ("a", "2")))
    reordered = registration_config_digest("http://child-a", (("a", "2"), ("z", "1")))
    assert first == reordered
    assert first != registration_config_digest(
        "http://child-b", (("a", "2"), ("z", "1"))
    )


@pytest.mark.parametrize(
    "override",
    [
        {"registry_revision": None},
        {"registry_revision": 0},
        {"registry_digest": ""},
        {"registration_config_digest": ""},
        {"component_revision": None},
        {"component_revision": 0},
        {"component_id": ""},
        {"component_digest": ""},
        {"discovery_tenant": None},
        {"local_catalog_epoch": 0},
        {"child_id": ""},
        {"child_connection_generation": 0},
        {"family_errors": {"prompts": "TimeoutError"}},
    ],
)
def test_incomplete_observation_fails_closed(override: dict[str, object]) -> None:
    with pytest.raises(CatalogSnapshotUnavailable):
        ChildCatalogSnapshot.from_observation(**_observation(**override))


@pytest.mark.parametrize(
    "drift",
    [
        {"component_revision": 9},
        {"component_id": "mcp:other/mcp_server/source-mcp"},
        {"component_digest": "sha256:" + "c" * 64},
        {"registry_revision": 13},
        {"registry_digest": "sha256:" + "d" * 64},
        {"registration_config_digest": "sha256:" + "e" * 64},
        {"discovery_tenant": "tenant-b"},
        {"local_catalog_epoch": 4},
        {"child_id": "mounted-child-b"},
        {"child_connection_generation": 3},
    ],
)
def test_snapshot_rejects_config_scope_and_generation_drift(
    drift: dict[str, object],
) -> None:
    snapshot = ChildCatalogSnapshot.from_observation(**_observation())
    source = {
        key: value
        for key, value in _observation().items()
        if key
        in {
            "registry_revision",
            "component_id",
            "registry_digest",
            "registration_config_digest",
            "component_revision",
            "component_digest",
            "discovery_tenant",
            "local_catalog_epoch",
            "child_id",
            "child_connection_generation",
        }
    }
    assert snapshot.matches_source(**source)
    assert not snapshot.matches_source(**{**source, **drift})


def test_served_snapshot_rechecks_config_child_and_tenant() -> None:
    snapshot = ChildCatalogSnapshot.from_observation(**_observation())
    mux = object.__new__(MCPMultiplexer)
    mux._claim_serving_loop = lambda: None
    mux._child_catalog_snapshots = {"source-mcp": snapshot}
    mux._catalog_epoch = 3
    runtime = SimpleNamespace(generation=2, _catalog_child_id="mounted-child-a")
    mux.children = {"source-mcp": runtime}
    component = SimpleNamespace(
        component_id="mcp:source-mcp/mcp_server/source-mcp", entry_revision=8
    )
    content = SimpleNamespace(content_digest="sha256:" + "b" * 64)
    registration = SimpleNamespace(
        name="source-mcp",
        url="http://child-a",
        resources=(("source", "tenant-a"),),
    )
    server = SimpleNamespace(
        component=component, content=content, registration=registration
    )
    fleet = SimpleNamespace(
        context=SimpleNamespace(tenant_id="tenant-a", principal_id="graphos-process"),
        servers=(server,),
        registry_revision=12,
        registry_digest="sha256:" + "a" * 64,
    )
    mux._fleet_catalog = fleet
    assert mux.child_catalog_snapshot("source-mcp") is snapshot
    attestation = mux.catalog_attestation("source-mcp")
    assert attestation.four_family_digest == snapshot.content_digest
    assert attestation.child_id == "mounted-child-a"
    assert attestation.component_id == snapshot.component_id
    assert attestation.registry_digest == snapshot.registry_digest
    assert attestation.discovery_tenant == "tenant-a"
    assert attestation.local_catalog_epoch == 3
    assert attestation.attester_principal_id == "graphos-process"
    component.component_id = "mcp:other/mcp_server/source-mcp"
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.catalog_attestation("source-mcp")
    component.component_id = "mcp:source-mcp/mcp_server/source-mcp"
    component.entry_revision = 9
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.child_catalog_snapshot("source-mcp")
    component.entry_revision = 8
    registration.url = "http://child-b"
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.child_catalog_snapshot("source-mcp")
    registration.url = "http://child-a"
    runtime.generation = 3
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.child_catalog_snapshot("source-mcp")
    runtime.generation = 2
    runtime._catalog_child_id = "mounted-child-b"
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.child_catalog_snapshot("source-mcp")
    runtime._catalog_child_id = "mounted-child-a"
    fleet.context.tenant_id = "tenant-b"
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.child_catalog_snapshot("source-mcp")
    fleet.context.tenant_id = "tenant-a"
    fleet.context.principal_id = ""
    with pytest.raises(CatalogSnapshotUnavailable):
        mux.catalog_attestation("source-mcp")


def test_local_epoch_cannot_substitute_for_five_field_pack_binding() -> None:
    snapshot = ChildCatalogSnapshot.from_observation(**_observation())
    mux = object.__new__(MCPMultiplexer)
    mux.child_catalog_snapshot = lambda _server: snapshot
    with pytest.raises(CatalogSnapshotUnavailable, match="configuration revision"):
        mux.pack_catalog_binding("source-mcp")


@pytest.mark.parametrize(
    "binding",
    [
        None,
        SimpleNamespace(tenant_id="tenant-a"),
        TenantLocalDiscoveryBinding(tenant_id="tenant-b"),
    ],
)
def test_unverified_discovery_cannot_publish_snapshot(binding: object) -> None:
    mux = object.__new__(MCPMultiplexer)
    mux._child_catalog_snapshots = {"source-mcp": object()}
    mux._fleet_catalog = SimpleNamespace(
        context=SimpleNamespace(tenant_id="tenant-a"),
        servers=(
            SimpleNamespace(
                registration=SimpleNamespace(name="source-mcp"),
                component=SimpleNamespace(entry_revision=8),
                content=SimpleNamespace(content_digest="sha256:" + "b" * 64),
            ),
        ),
        registry_revision=12,
        registry_digest="sha256:" + "a" * 64,
    )
    mux._publish_child_catalog_snapshot(
        "source-mcp",
        [{"name": "tool"}],
        {
            "resources": [],
            "resource_templates": [],
            "native_prompts": [],
            "catalog_family_errors": {},
        },
        3,
        2,
        binding,
    )
    assert "source-mcp" not in mux._child_catalog_snapshots
