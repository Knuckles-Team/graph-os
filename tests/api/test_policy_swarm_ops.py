"""Focused authority tests for the policy and swarm read ports.

Covers GRAPHOS-OPS-R021.3 (policy and swarm read operations): each is a
single caller-scoped, read-only operation bound to one typed port, matching
the ingest-runner-facade convention (GRAPHOS-OPS-R020.1/R020.2).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import policy, swarm
from graph_os.api.ops.policy import PolicyState
from graph_os.api.ops.swarm import SwarmTopology


def _context(*, reader: object | None, service_name: str):
    caller = SimpleNamespace(tenant="tenant-a", principal="caller-a")
    services = {} if reader is None else {service_name: reader}
    return SimpleNamespace(caller=caller, services=services, idempotency_key=None)


class _FakePolicyReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> PolicyState:
        self.calls.append(tenant)
        return PolicyState(tenant=tenant, rules={"default": "allow"})


async def test_policy_read_returns_the_tenant_scoped_state() -> None:
    reader = _FakePolicyReader()
    context = _context(reader=reader, service_name="policy_reader")
    result = await policy.handle_policy_read(context, {}, None)
    assert result == {"value": {"tenant": "tenant-a", "rules": {"default": "allow"}}}
    assert reader.calls == ["tenant-a"]


async def test_policy_read_fails_closed_when_reader_not_composed() -> None:
    context = _context(reader=None, service_name="policy_reader")
    with pytest.raises(OperationRefused) as excinfo:
        await policy.handle_policy_read(context, {}, None)
    assert excinfo.value.code == "UNAVAILABLE"


def test_policy_operation_is_caller_scoped_and_read_only() -> None:
    (op,) = policy.operations()
    assert op.id == "policy.read"
    assert op.scopes == frozenset({"policy:read"})
    assert op.effect.value == "read"
    assert op.executor.value == "caller"


class _FakeSwarmTopologyReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> SwarmTopology:
        self.calls.append(tenant)
        return SwarmTopology(tenant=tenant, nodes=("node-1",))


async def test_swarm_topology_read_returns_the_tenant_scoped_topology() -> None:
    reader = _FakeSwarmTopologyReader()
    context = _context(reader=reader, service_name="swarm_topology_reader")
    result = await swarm.handle_swarm_topology_read(context, {}, None)
    assert result == {"value": {"tenant": "tenant-a", "nodes": ["node-1"]}}
    assert reader.calls == ["tenant-a"]


async def test_swarm_topology_read_fails_closed_when_reader_not_composed() -> None:
    context = _context(reader=None, service_name="swarm_topology_reader")
    with pytest.raises(OperationRefused) as excinfo:
        await swarm.handle_swarm_topology_read(context, {}, None)
    assert excinfo.value.code == "UNAVAILABLE"


def test_swarm_operation_is_caller_scoped_and_read_only() -> None:
    (op,) = swarm.operations()
    assert op.id == "swarm.topology.read"
    assert op.scopes == frozenset({"swarm:read"})
    assert op.effect.value == "read"
    assert op.executor.value == "caller"
