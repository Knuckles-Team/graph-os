"""Focused contract tests for graph-os's epistemic client adapter.

The scaffold cannot declare its EG runtime dependency until the integration
lane owns ``pyproject.toml``.  Load the module by path so these tests still
exercise the complete adapter contract without changing shared package files.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_adapter() -> ModuleType:
    path = Path(__file__).parents[1] / "graph_os" / "epistemic.py"
    spec = importlib.util.spec_from_file_location("graph_os_epistemic_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load_adapter()


@dataclass
class Actor:
    actor_id: str = "service:graph-os"
    tenant_id: str = "tenant:a"
    roles: tuple[str, ...] = ("graph-client",)
    authenticated: bool = True
    current: bool = True

    def ensure_credential_current(self) -> None:
        if not self.current:
            raise PermissionError("expired")


class Placement:
    def __init__(self, owner: Client) -> None:
        self.owner = owner

    async def route(
        self, tenant: str, graph: str, *, client_epoch: int
    ) -> dict[str, Any]:
        self.owner.seen_contexts.append(self.owner.context)
        return {
            "authoritative": True,
            "tenant_ref": tenant,
            "partition_ref": graph,
            "route_id": "route:1",
            "placed": True,
            "stale": False,
            "group": 2,
            "epoch": max(1, client_epoch),
            "fencing_token": 2,
            "endpoints": ["unix:///run/eg.sock"],
        }


class Topology:
    def __init__(self, owner: Client) -> None:
        self.owner = owner

    async def members(self, **_expectations: Any) -> dict[str, Any]:
        self.owner.seen_contexts.append(self.owner.context)
        return {
            "cluster_id": "sha256:" + "a" * 64,
            "membership_epoch": 3,
            "placement_epoch": 4,
            "groups": [],
        }


class Client:
    def __init__(self, graph: str, context: dict[str, Any]) -> None:
        self.graph = graph
        self.base_context = context
        self.context = context
        self.seen_contexts: list[dict[str, Any]] = []
        self.placement = Placement(self)
        self.cluster_topology = Topology(self)
        self.closed = False

    @contextlib.contextmanager
    def use_verified_context(self, context: dict[str, Any]):
        previous = self.context
        self.context = context
        try:
            yield self
        finally:
            self.context = previous

    async def health(self) -> dict[str, Any]:
        self.seen_contexts.append(self.context)
        return {"status": "ok"}

    async def close(self) -> None:
        self.closed = True


def context(*scopes: str, actor: Actor | None = None):
    return adapter.ClientContext.from_actor(
        actor or Actor(),
        tenant="tenant:a",
        audience="epistemic-graph",
        scopes=scopes,
        policy_version="policy:1",
    )


def test_actor_translation_fails_closed() -> None:
    with pytest.raises(adapter.AuthorityError, match="verified identity"):
        context("graph:read", actor=Actor(authenticated=False))
    with pytest.raises(adapter.AuthorityError, match="does not match"):
        adapter.ClientContext.from_actor(
            Actor(),
            tenant="tenant:b",
            audience="epistemic-graph",
            scopes=("graph:read",),
            policy_version="policy:1",
        )


def test_pool_reuses_one_client_per_graph_and_rebinds_context() -> None:
    calls: list[dict[str, Any]] = []

    async def connect(**kwargs: Any) -> Client:
        calls.append(kwargs)
        return Client(kwargs["graph_name"], kwargs["verified_context"])

    async def exercise() -> None:
        pool = adapter.EpistemicClientPool(connect, auth_secret="test-secret")
        first = context("graph:read")
        second = adapter.ClientContext(
            principal="service:worker",
            tenant="tenant:a",
            audience="epistemic-graph",
            agent_id="service:worker",
            roles=("worker",),
            scopes=("graph:read",),
            policy_version="policy:1",
        )
        async with pool.bind(first, "tenant:a:graph") as client:
            assert client.context["principal"] == "service:graph-os"
        async with pool.bind(second, "tenant:a:graph") as same_client:
            assert same_client is client
            assert same_client.context["principal"] == "service:worker"
        assert len(calls) == 1
        assert pool.graph_count == 1

    asyncio.run(exercise())


def test_pool_rejects_cross_tenant_graph_reuse() -> None:
    async def connect(**kwargs: Any) -> Client:
        return Client(kwargs["graph_name"], kwargs["verified_context"])

    async def exercise() -> None:
        pool = adapter.EpistemicClientPool(connect)
        async with pool.bind(context("graph:read"), "shared-name"):
            pass
        other = adapter.ClientContext(
            principal="service:other",
            tenant="tenant:b",
            audience="epistemic-graph",
            agent_id="service:other",
            roles=(),
            scopes=("graph:read",),
            policy_version="policy:1",
        )
        with pytest.raises(adapter.TenantGraphBindingError):
            async with pool.bind(other, "shared-name"):
                pass

    asyncio.run(exercise())


def test_topology_adapter_enforces_exact_policy_and_propagates_context() -> None:
    clients: list[Client] = []

    async def connect(**kwargs: Any) -> Client:
        client = Client(kwargs["graph_name"], kwargs["verified_context"])
        clients.append(client)
        return client

    async def exercise() -> None:
        pool = adapter.EpistemicClientPool(connect)
        topology = adapter.TopologyAdapter(pool)
        with pytest.raises(adapter.AuthorityError, match="cluster:placement-read"):
            await topology.placement(context("graph:read"), "tenant:a:graph")

        authority = context(
            adapter.PLACEMENT_READ_SCOPE,
            adapter.TOPOLOGY_READ_SCOPE,
            adapter.SERVICE_CONTROL_SCOPE,
        )
        placement = await topology.placement(authority, "tenant:a:graph")
        snapshot = await topology.topology(authority, "tenant:a:graph")
        health = await topology.health(authority, "tenant:a:graph")

        assert placement.tenant == "tenant:a"
        assert placement.graph == "tenant:a:graph"
        assert snapshot.membership_epoch == 3
        assert health == {"status": "ok"}
        assert clients[0].seen_contexts == [authority.to_claims()] * 3

    asyncio.run(exercise())
