"""The SDK pack resolver selects the exact mounted connector after AU policy."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from agent_utilities.knowledge_graph.core.session import (
    GraphSession,
    SessionRequiredError,
    use_session,
)
from agent_utilities.orchestration.action_policy import (
    ActionDecision,
    ActionRequest,
    PolicyDisposition,
    PolicyReceipt,
)
from agent_utilities.security.actor_identity import ActorType
from agent_utilities.security.brain_context import ActorContext
from epistemic_graph.generated.connector_pack import McpCatalogSnapshotBinding

from graph_os.connector_pack_authority import compose_pack_import_authority


def _session() -> GraphSession:
    return GraphSession(
        actor=ActorContext(
            actor_id="service:graph-os",
            actor_type=ActorType.AUTOMATED_SERVICE,
            tenant_id="tenant-a",
            authenticated=True,
        ),
        tenant="tenant-a",
        scopes=frozenset({"agent:pack-control"}),
        graph="tenant-a",
        audience="graph-os",
        policy_version="policy-a",
    )


class _Policy:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.targets: list[str] = []

    def decide(self, request: ActionRequest) -> ActionDecision:
        self.targets.append(request.target)
        receipt = PolicyReceipt(
            receipt_id="action_decision:test",
            request_digest=request.digest(),
            disposition=(
                PolicyDisposition.APPROVE if self.allow else PolicyDisposition.DENY
            ),
            policy_origin="test",
        )
        return ActionDecision(
            decision="allow" if self.allow else "deny",
            tier="auto" if self.allow else "forbidden",
            request=request,
            receipt=receipt,
        )


class _Catalog:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def reconcile_pack_catalog_binding(
        self, server_name: str
    ) -> McpCatalogSnapshotBinding:
        self.seen.append(server_name)
        generation = len(self.seen)
        return McpCatalogSnapshotBinding(
            authorization_scope_digest="ab" * 32,
            catalog_generation=generation,
            child_connection_generation=3,
            configuration_revision=4,
            snapshot_digest="cd" * 32,
        )


class _Owner:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def serving_principal(self, server_name: str, tenant_id: str) -> str:
        self.seen.append((server_name, tenant_id))
        return "principal:sha256:" + ("ef" * 32)


def test_missing_authenticated_owner_source_fails_at_composition() -> None:
    with pytest.raises(TypeError, match="authenticated EG owner principal"):
        compose_pack_import_authority(
            object(), _session(), mounted_catalog=_Catalog(), owner_principal=None
        )


@pytest.mark.asyncio
async def test_resolves_each_policy_target_against_same_mounted_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _Policy()
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy", lambda _engine: policy
    )
    session = _session()
    catalog = _Catalog()
    owner = _Owner()
    resolver = compose_pack_import_authority(
        object(), session, mounted_catalog=catalog, owner_principal=owner
    )
    with use_session(session):
        first, first_context = await resolver(" mcp-first ")
        second, second_context = await resolver("mcp-second")

    assert policy.targets == ["mcp-first", "mcp-second"]
    assert catalog.seen == ["mcp-first", "mcp-second"]
    assert owner.seen == [
        ("mcp-first", "tenant-a"),
        ("mcp-second", "tenant-a"),
    ]
    assert first.catalog_generation == 1
    assert second.catalog_generation == 2
    assert first_context.tenant_id == second_context.tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_policy_denial_never_reads_catalog_or_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _Policy(allow=False)
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy", lambda _engine: policy
    )
    session = _session()
    catalog = _Catalog()
    owner = _Owner()
    resolver = compose_pack_import_authority(
        object(), session, mounted_catalog=catalog, owner_principal=owner
    )
    with use_session(session), pytest.raises(RuntimeError, match="not authorized"):
        await resolver("mcp-first")
    assert catalog.seen == []
    assert owner.seen == []


@pytest.mark.asyncio
async def test_owner_read_failure_refuses_pack_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy",
        lambda _engine: _Policy(),
    )
    session = _session()

    class BrokenOwner:
        async def serving_principal(self, _server: str, _tenant: str) -> Any:
            raise RuntimeError("EG owner read unavailable")

    resolver = compose_pack_import_authority(
        object(), session, mounted_catalog=_Catalog(), owner_principal=BrokenOwner()
    )
    with use_session(session), pytest.raises(RuntimeError, match="serving principal"):
        await resolver("mcp-first")


@pytest.mark.asyncio
async def test_changed_ambient_session_never_reads_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_utilities.api.provisioning.get_action_policy",
        lambda _engine: _Policy(),
    )
    session = _session()
    other = replace(session, policy_version="policy-b")
    catalog = _Catalog()
    owner = _Owner()
    resolver = compose_pack_import_authority(
        object(), session, mounted_catalog=catalog, owner_principal=owner
    )
    with use_session(other), pytest.raises(SessionRequiredError):
        await resolver("mcp-first")
    assert catalog.seen == []
    assert owner.seen == []
