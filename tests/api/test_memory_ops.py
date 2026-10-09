"""Focused authority tests for the agent memory store (GRAPHOS-OPS-R024.4).

Covers memory.read and memory.write: both bound to one typed port, scoped
to the caller's own tenant, failing closed with UNAVAILABLE when the port
is not composed, matching the ingest-runner-facade convention
(GRAPHOS-OPS-R020.1/R020.2).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import memory
from graph_os.api.ops.memory import MemoryRecord


def _context(
    *,
    store: object | None,
    idempotency_key: str | None = "idem-1",
    tenant: str = "tenant-a",
):
    caller = SimpleNamespace(tenant=tenant, principal="caller-a")
    services = {} if store is None else {"memory_store": store}
    return SimpleNamespace(
        caller=caller, services=services, idempotency_key=idempotency_key
    )


class _FakeMemoryStore:
    def __init__(self) -> None:
        self.read_calls: list[tuple[str, str]] = []
        self.write_calls: list[tuple[str, str, dict, str]] = []

    async def read(self, *, tenant: str, key: str) -> MemoryRecord:
        self.read_calls.append((tenant, key))
        return MemoryRecord(tenant=tenant, key=key, value={"seen": True})

    async def write(
        self, *, tenant: str, key: str, value: dict, idempotency_key: str
    ) -> MemoryRecord:
        self.write_calls.append((tenant, key, value, idempotency_key))
        return MemoryRecord(tenant=tenant, key=key, value=value)


async def test_memory_read_is_scoped_to_the_callers_own_tenant(op_by_id) -> None:
    store = _FakeMemoryStore()
    context = _context(store=store, tenant="tenant-a")
    op = op_by_id(memory.operations(), "memory.read")
    result = await memory.handle_memory_read(context, {"key": "k1"}, op)
    assert result == {
        "value": {"tenant": "tenant-a", "key": "k1", "value": {"seen": True}}
    }
    assert store.read_calls == [("tenant-a", "k1")]


async def test_memory_read_fails_closed_when_store_not_composed(op_by_id) -> None:
    context = _context(store=None)
    op = op_by_id(memory.operations(), "memory.read")
    with pytest.raises(OperationRefused) as excinfo:
        await memory.handle_memory_read(context, {"key": "k1"}, op)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_memory_write_is_scoped_to_the_callers_own_tenant(op_by_id) -> None:
    store = _FakeMemoryStore()
    context = _context(store=store, tenant="tenant-b")
    op = op_by_id(memory.operations(), "memory.write")
    result = await memory.handle_memory_write(
        context, {"key": "k2", "value": {"x": 1}}, op
    )
    assert result == {"value": {"tenant": "tenant-b", "key": "k2", "value": {"x": 1}}}
    assert store.write_calls == [("tenant-b", "k2", {"x": 1}, "idem-1")]


async def test_memory_write_fails_closed_when_store_not_composed(op_by_id) -> None:
    context = _context(store=None)
    op = op_by_id(memory.operations(), "memory.write")
    with pytest.raises(OperationRefused) as excinfo:
        await memory.handle_memory_write(context, {"key": "k2", "value": {}}, op)
    assert excinfo.value.code == "UNAVAILABLE"


async def test_memory_write_requires_an_idempotency_key(op_by_id) -> None:
    context = _context(store=_FakeMemoryStore(), idempotency_key=None)
    op = op_by_id(memory.operations(), "memory.write")
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await memory.handle_memory_write(context, {"key": "k2", "value": {}}, op)


def test_memory_operations_declare_distinct_read_and_write_scopes() -> None:
    ops = {op.id: op for op in memory.operations()}
    read_op = ops["memory.read"]
    write_op = ops["memory.write"]
    assert read_op.scopes == frozenset({"memory:read"})
    assert read_op.effect.value == "read"
    assert write_op.scopes == frozenset({"memory:write"})
    assert write_op.effect.value == "write"
    assert write_op.idempotency.value == "key_required"
