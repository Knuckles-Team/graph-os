"""Focused authority tests for the policy and swarm read ports (GRAPHOS-OPS-R021.3).

``policy.read`` and ``swarm.topology.read`` are each a lone caller-scoped
port bound through ``tests/api/_ops_support.py``'s ``tenant_reader_context``;
see ``graph_os.api.ops.ingest`` for the facade convention they follow
(GRAPHOS-OPS-R020.1/R020.2).
"""

from __future__ import annotations

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import policy, swarm
from graph_os.api.ops.policy import PolicyState
from graph_os.api.ops.swarm import SwarmTopology
from tests.api._ops_support import tenant_reader_context as _context


class _FakePolicyReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, *, tenant: str) -> PolicyState:
        self.calls.append(tenant)
        return PolicyState(tenant=tenant, rules={"default": "allow"})


async def test_policy_read_returns_the_tenant_scoped_state(op_by_id) -> None:
    reader = _FakePolicyReader()
    context = _context(reader=reader, service_name="policy_reader")
    op = op_by_id(policy.operations(), "policy.read")
    result = await policy.handle_policy_read(context, {}, op)
    assert result == {"value": {"tenant": "tenant-a", "rules": {"default": "allow"}}}
    assert reader.calls == ["tenant-a"]


async def test_policy_read_fails_closed_when_reader_not_composed(op_by_id) -> None:
    context = _context(reader=None, service_name="policy_reader")
    op = op_by_id(policy.operations(), "policy.read")
    with pytest.raises(OperationRefused) as excinfo:
        await policy.handle_policy_read(context, {}, op)
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


async def test_swarm_topology_read_returns_the_tenant_scoped_topology(
    op_by_id,
) -> None:
    reader = _FakeSwarmTopologyReader()
    context = _context(reader=reader, service_name="swarm_topology_reader")
    op = op_by_id(swarm.operations(), "swarm.topology.read")
    result = await swarm.handle_swarm_topology_read(context, {}, op)
    assert result == {"value": {"tenant": "tenant-a", "nodes": ["node-1"]}}
    assert reader.calls == ["tenant-a"]


async def test_swarm_topology_read_fails_closed_when_reader_not_composed(
    op_by_id,
) -> None:
    context = _context(reader=None, service_name="swarm_topology_reader")
    op = op_by_id(swarm.operations(), "swarm.topology.read")
    with pytest.raises(OperationRefused) as excinfo:
        await swarm.handle_swarm_topology_read(context, {}, op)
    assert excinfo.value.code == "UNAVAILABLE"


def test_swarm_operation_is_caller_scoped_and_read_only() -> None:
    (op,) = swarm.operations()
    assert op.id == "swarm.topology.read"
    assert op.scopes == frozenset({"swarm:read"})
    assert op.effect.value == "read"
    assert op.executor.value == "caller"
