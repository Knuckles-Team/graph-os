"""A pack binding comes only from the same verified EG client as the fleet read."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from epistemic_graph.generated import connector_pack as generated_pack
from epistemic_graph.generated.connector_pack import AgentLibraryMutationContext

import graph_os.fleet.catalog_authority as authority
from graph_os.fleet.catalog_reader import ReadContext
from graph_os.fleet.catalog_snapshot import McpCatalogAttestation
from graph_os.fleet.epistemic_adapter import GeneratedFleetCatalogPort


def _context() -> ReadContext:
    return ReadContext(
        tenant_id="tenant-a",
        principal_id="service:graph-os",
        agent_id="service:graph-os",
        audience="epistemic-graph",
        policy_version="policy-7",
    )


def _observation() -> McpCatalogAttestation:
    digest = "sha256:" + "a" * 64
    return McpCatalogAttestation(
        server_name="source-mcp",
        attester_principal_id="service:graph-os",
        component_id="mcp:source-mcp/mcp_server/source-mcp",
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


class Mounted:
    def __init__(self, observation: McpCatalogAttestation) -> None:
        self.observation = observation

    def catalog_attestation(self, _server: str) -> McpCatalogAttestation:
        return self.observation


class Request:
    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields

    @classmethod
    def model_validate(cls, fields: dict[str, Any]) -> Request:
        return cls(fields)

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self.fields


@pytest.mark.asyncio
async def test_owner_read_uses_same_verified_tenant_client_and_child(
    monkeypatch: Any,
) -> None:
    read = _context()
    client = object()
    mounted = Mounted(_observation())
    port = GeneratedFleetCatalogPort(
        tenant_client=client, commons_client=object(), context=read
    )
    sent: list[tuple[Any, Any]] = []

    async def send(actual_client: Any, params: Any) -> Any:
        sent.append((actual_client, params))
        return SimpleNamespace(payload="principal:sha256:" + "b" * 64)

    monkeypatch.setattr(
        generated_pack, "McpCatalogAuthorityStatusRequest", Request, raising=False
    )
    monkeypatch.setattr(authority, "send_connector_pack", send)
    source = authority.VerifiedAgentLibraryOwnerPrincipal(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
    )
    assert await source.serving_principal("source-mcp", "tenant-a") == (
        "principal:sha256:" + "b" * 64
    )
    assert sent == [
        (
            client,
            {
                "op": {
                    "op": "catalog_owner_principal",
                    "request": {"tenant_id": "tenant-a", "server_name": "source-mcp"},
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_owner_read_refuses_wrong_tenant_or_child_swap(monkeypatch: Any) -> None:
    read = _context()
    mounted = Mounted(_observation())
    port = GeneratedFleetCatalogPort(
        tenant_client=object(), commons_client=object(), context=read
    )
    sent = False

    async def send(_client: Any, _params: Any) -> Any:
        nonlocal sent
        sent = True
        mounted.observation = replace(mounted.observation, child_id="other-child")
        return SimpleNamespace(payload="principal:sha256:" + "b" * 64)

    monkeypatch.setattr(
        generated_pack, "McpCatalogAuthorityStatusRequest", Request, raising=False
    )
    monkeypatch.setattr(authority, "send_connector_pack", send)
    source = authority.VerifiedAgentLibraryOwnerPrincipal(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
    )
    with pytest.raises(authority.CatalogAuthorityUnavailable):
        await source.serving_principal("source-mcp", "tenant-b")
    assert not sent
    with pytest.raises(authority.CatalogAuthorityUnavailable, match="child changed"):
        await source.serving_principal("source-mcp", "tenant-a")


@pytest.mark.asyncio
async def test_writer_uses_reader_client_and_eg_binding(monkeypatch: Any) -> None:
    read = _context()
    client = object()
    port = GeneratedFleetCatalogPort(
        tenant_client=client, commons_client=object(), context=read
    )
    mounted = Mounted(_observation())
    context = AgentLibraryMutationContext.model_construct(tenant_id="tenant-a")
    sent: list[tuple[Any, Any]] = []

    async def mutation(_server: str) -> AgentLibraryMutationContext:
        return context

    async def send(actual_client: Any, params: Any) -> Any:
        sent.append((actual_client, params))
        if params["op"]["op"] == "catalog_authority_status":
            return SimpleNamespace(
                payload={
                    "configuration_revision": 4,
                    "catalog_generation": 6,
                    "snapshot_digest": "d" * 64,
                    "child_connection_generation": 2,
                    "authorization_scope_digest": "c" * 64,
                }
            )
        return SimpleNamespace(
            payload={
                "configuration_revision": 4,
                "catalog_generation": 7,
                "snapshot_digest": "b" * 64,
                "child_connection_generation": 2,
                "authorization_scope_digest": "c" * 64,
            }
        )

    monkeypatch.setattr(
        generated_pack, "McpCatalogReconcileRequest", Request, raising=False
    )
    monkeypatch.setattr(
        generated_pack, "McpCatalogAuthorityStatusRequest", Request, raising=False
    )
    monkeypatch.setattr(authority, "send_connector_pack", send)
    writer = authority.VerifiedMcpCatalogWriter(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
        mutation_context=mutation,
    )
    # A fresh GraphOS process has no local generation, but the persisted EG
    # status supplies the exact CAS value before the write.
    binding = await writer.reconcile("source-mcp", expected_catalog_generation=None)
    assert binding.catalog_generation == 7
    assert sent[0][0] is client
    assert len(sent) == 2
    assert sent[0][1]["op"]["op"] == "catalog_authority_status"
    fields = sent[1][1]["op"]["request"]
    assert fields["four_family_digest"] == "a" * 64
    assert fields["registry_digest"] == "a" * 64
    assert fields["expected_catalog_generation"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"attester_principal_id": "service:other"},
        {"discovery_tenant": "tenant-b"},
        {"server_name": "other-mcp"},
    ],
)
async def test_writer_rejects_observation_identity_mismatch(
    change: dict[str, Any],
) -> None:
    read = _context()
    mounted = Mounted(replace(_observation(), **change))
    port = GeneratedFleetCatalogPort(
        tenant_client=object(), commons_client=object(), context=read
    )

    async def mutation(_server: str) -> AgentLibraryMutationContext:
        raise AssertionError("no mutation context should be requested")

    writer = authority.VerifiedMcpCatalogWriter(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
        mutation_context=mutation,
    )
    with pytest.raises(authority.CatalogAuthorityUnavailable):
        await writer.reconcile("source-mcp", expected_catalog_generation=None)


@pytest.mark.asyncio
async def test_writer_rejects_changed_verified_context_before_send() -> None:
    read = _context()
    port = GeneratedFleetCatalogPort(
        tenant_client=object(), commons_client=object(), context=read
    )

    async def mutation(_server: str) -> AgentLibraryMutationContext:
        raise AssertionError("no mutation context should be requested")

    writer = authority.VerifiedMcpCatalogWriter(
        fleet_port=port,
        mounted_catalog=Mounted(_observation()),
        current_context=lambda: replace(read, principal_id="service:other"),
        mutation_context=mutation,
    )
    with pytest.raises(authority.CatalogAuthorityUnavailable):
        await writer.reconcile("source-mcp", expected_catalog_generation=None)


@pytest.mark.asyncio
async def test_writer_rejects_child_swap_during_policy_resolution(
    monkeypatch: Any,
) -> None:
    read = _context()
    mounted = Mounted(_observation())
    port = GeneratedFleetCatalogPort(
        tenant_client=object(), commons_client=object(), context=read
    )

    async def mutation(_server: str) -> AgentLibraryMutationContext:
        mounted.observation = replace(mounted.observation, child_id="mounted-child-b")
        return AgentLibraryMutationContext.model_construct(tenant_id="tenant-a")

    async def send(_client: Any, _params: Any) -> Any:
        return SimpleNamespace(payload=None)

    monkeypatch.setattr(
        generated_pack, "McpCatalogAuthorityStatusRequest", Request, raising=False
    )
    monkeypatch.setattr(authority, "send_connector_pack", send)

    writer = authority.VerifiedMcpCatalogWriter(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
        mutation_context=mutation,
    )
    with pytest.raises(authority.CatalogAuthorityUnavailable, match="before"):
        await writer.reconcile("source-mcp", expected_catalog_generation=None)


@pytest.mark.asyncio
async def test_writer_rejects_child_swap_during_eg_await(monkeypatch: Any) -> None:
    read = _context()
    mounted = Mounted(_observation())
    port = GeneratedFleetCatalogPort(
        tenant_client=object(), commons_client=object(), context=read
    )

    async def mutation(_server: str) -> AgentLibraryMutationContext:
        return AgentLibraryMutationContext.model_construct(tenant_id="tenant-a")

    async def send(_client: Any, _params: Any) -> Any:
        if _params["op"]["op"] == "catalog_authority_status":
            return SimpleNamespace(payload=None)
        mounted.observation = replace(mounted.observation, child_id="mounted-child-b")
        return SimpleNamespace(payload={})

    monkeypatch.setattr(
        generated_pack, "McpCatalogReconcileRequest", Request, raising=False
    )
    monkeypatch.setattr(
        generated_pack, "McpCatalogAuthorityStatusRequest", Request, raising=False
    )
    monkeypatch.setattr(authority, "send_connector_pack", send)
    writer = authority.VerifiedMcpCatalogWriter(
        fleet_port=port,
        mounted_catalog=mounted,
        current_context=lambda: read,
        mutation_context=mutation,
    )
    with pytest.raises(authority.CatalogAuthorityUnavailable, match="changed"):
        await writer.reconcile("source-mcp", expected_catalog_generation=None)


@pytest.mark.asyncio
async def test_independent_catalog_policy_context_requires_exact_receipt(
    monkeypatch: Any,
) -> None:
    from agent_utilities.knowledge_graph.core import session as session_module
    from agent_utilities.orchestration import action_policy

    verified = SimpleNamespace(
        actor=SimpleNamespace(actor_id="service:graph-os"),
        tenant="tenant-a",
        policy_version="policy-7",
        trace_context="trace-a",
    )
    scopes: list[str] = []

    def resolve(_session: Any, *, required_scope: str) -> Any:
        scopes.append(required_scope)
        return verified

    class Policy:
        valid = True

        def decide(self, request: Any) -> Any:
            digest = request.digest() if self.valid else "0" * 64
            return SimpleNamespace(
                allowed=True,
                receipt=SimpleNamespace(
                    authorizes_effect=True,
                    request_digest=digest,
                    receipt_id="policy-receipt-a",
                ),
            )

    policy = Policy()
    monkeypatch.setattr(session_module, "resolve_session", resolve)
    monkeypatch.setattr(action_policy, "get_action_policy", lambda _engine: policy)
    provider = authority.policy_admitted_catalog_context(object(), verified)
    context = await provider("source-mcp")
    assert context.tenant_id == "tenant-a"
    assert context.purpose_id == "mcp-catalog:reconcile"
    assert context.policy_digest.startswith("sha256:")
    assert scopes == ["connector:catalog-attest", "admin:connector-pack"]

    policy.valid = False
    with pytest.raises(authority.CatalogAuthorityUnavailable, match="policy"):
        await provider("source-mcp")
