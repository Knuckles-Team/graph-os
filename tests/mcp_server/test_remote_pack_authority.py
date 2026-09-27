"""Remote pack authority binds the request to the served child and EG row."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from agent_utilities.knowledge_graph.core.session import GraphSession, use_session
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext
from epistemic_graph.generated.connector_pack import McpCatalogSnapshotBinding

import graph_os.mcp_server.remote_pack_authority as remote
from graph_os.fleet.catalog_snapshot import McpCatalogAttestation


def _session(principal: str, scopes: set[str]) -> GraphSession:
    return GraphSession(
        actor=ActorContext(
            actor_id=principal,
            actor_type=ActorType.AUTOMATED_SERVICE,
            tenant_id="tenant-a",
            authenticated=True,
        ),
        tenant="tenant-a",
        scopes=frozenset(scopes),
        graph="tenant-a",
        audience="graph-os",
        policy_version="policy-a",
    )


def _observation() -> McpCatalogAttestation:
    digest = "sha256:" + "a" * 64
    return McpCatalogAttestation(
        server_name="source-mcp",
        attester_principal_id="service:graph-os",
        component_id="mcp:source-mcp",
        component_revision=8,
        component_digest=digest,
        registry_revision=12,
        registry_digest=digest,
        registration_config_digest=digest,
        four_family_digest=digest,
        child_id="mounted-child-a",
        discovery_tenant="tenant-a",
        local_catalog_epoch=3,
        child_connection_generation=2,
    )


class _Mounted:
    def __init__(self):
        self.observation = _observation()
        self.binding = McpCatalogSnapshotBinding(
            authorization_scope_digest="ab" * 32,
            catalog_generation=7,
            child_connection_generation=2,
            configuration_revision=8,
            snapshot_digest="cd" * 32,
        )
        self.reconciles = 0

    def catalog_attestation(self, _connector):
        return self.observation

    async def reconcile_pack_catalog_binding(self, _connector):
        self.reconciles += 1
        return self.binding


class _Request:
    @classmethod
    def model_validate(cls, fields):
        return SimpleNamespace(model_dump=lambda **_kwargs: fields)


def _setup(monkeypatch, *, stale: bool = False):
    requester = _session("service:runner", {"agent:pack-control"})
    attester = _session(
        "service:graph-os", {"connector:catalog-attest", "admin:connector-pack"}
    )
    mounted = _Mounted()
    client = object()
    graph_compute = SimpleNamespace(
        for_graph=lambda tenant: SimpleNamespace(async_client=client)
    )
    engine = SimpleNamespace(graph_compute=graph_compute)
    sent = []

    async def send(actual_client, params):
        assert actual_client is client
        op = params["op"]["op"]
        sent.append(op)
        if op == "catalog_binding_status":
            binding = mounted.binding
            if stale:
                binding = binding.model_copy(update={"catalog_generation": 8})
            return SimpleNamespace(payload=binding.model_dump(mode="json"))
        assert op == "catalog_request_owner_principal"
        return SimpleNamespace(payload="principal:sha256:" + "e" * 64)

    def resolver(_engine, _requester, *, catalog_binding, serving_principal):
        async def resolve(_connector):
            binding = await catalog_binding()
            await serving_principal()
            caller = "principal:sha256:" + hashlib.sha256(b"service:runner").hexdigest()
            mutation = SimpleNamespace(
                tenant_id="tenant-a",
                caller_principal=caller,
                model_dump=lambda **_kwargs: {
                    "tenant_id": "tenant-a",
                    "caller_principal": caller,
                },
            )
            return binding, mutation

        return resolve

    monkeypatch.setattr(remote, "send_connector_pack", send)
    monkeypatch.setattr(remote, "pack_import_authority", resolver)

    async def refresh(_session):
        return None

    monkeypatch.setattr(remote, "_refresh_attester_session", refresh)
    monkeypatch.setattr(
        remote.generated_pack,
        "McpCatalogAuthorityStatusRequest",
        _Request,
        raising=False,
    )
    service = remote.RemoteConnectorPackAuthority(
        engine=engine,
        attester_session=attester,
        mounted_catalog=mounted,
        request_session=lambda: requester,
    )
    return service, requester, mounted, sent


@pytest.mark.asyncio
async def test_exact_request_and_eg_binding_returned(monkeypatch) -> None:
    service, requester, mounted, sent = _setup(monkeypatch)
    with use_session(requester):
        response = await service("source-mcp")
    assert response["connector"] == "source-mcp"
    assert response["catalog_binding"] == mounted.binding.model_dump(mode="json")
    assert response["mutation_context"]["tenant_id"] == "tenant-a"
    assert sent == [
        "catalog_binding_status",
        "catalog_request_owner_principal",
        "catalog_binding_status",
    ]
    assert mounted.reconciles == 1


@pytest.mark.asyncio
async def test_stale_eg_binding_denies_owner_read(monkeypatch) -> None:
    service, requester, _mounted, sent = _setup(monkeypatch, stale=True)
    with (
        use_session(requester),
        pytest.raises(remote.CatalogAuthorityUnavailable, match="binding changed"),
    ):
        await service("source-mcp")
    assert sent == ["catalog_binding_status"]
