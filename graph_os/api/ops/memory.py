"""Memory read/write operations (GRAPHOS-OPS-R024.4, memory slice).

Implements the memory portion of GRAPHOS-OPS-R024: ``memory.read`` and
``memory.write`` operations over the agent memory store, bound by the
caller's own authority and scoped to the caller's own tenant, through one
typed port, matching the ingest-runner facade convention in
:mod:`graph_os.api.ops.ingest`. An uncomposed store fails closed with a
typed ``UNAVAILABLE`` refusal rather than fabricating a record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.ops._common import (
    Params,
    bound_service,
    build_read_op,
    build_write_op,
)
from graph_os.api.registry import OpSpec


class MemoryReadParams(Params):
    key: str = Field(min_length=1, max_length=256)


class MemoryWriteParams(Params):
    key: str = Field(min_length=1, max_length=256)
    value: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    """One tenant-scoped memory record; the store owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)


class MemoryResult(Params):
    value: dict[str, Any]


@runtime_checkable
class MemoryStore(Protocol):
    """The one typed port GraphOS calls the agent memory store through."""

    async def read(self, *, tenant: str, key: str) -> MemoryRecord: ...

    async def write(
        self, *, tenant: str, key: str, value: dict[str, Any], idempotency_key: str
    ) -> MemoryRecord: ...


def _bound_store(context: Any) -> MemoryStore:
    return bound_service(context, "memory_store", reason="memory store is not composed")


async def handle_memory_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return one memory record scoped to the caller's own tenant."""
    store = _bound_store(context)
    record = await store.read(tenant=context.caller.tenant, key=params["key"])
    return {"value": record.model_dump(mode="json")}


async def handle_memory_write(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Write one memory record scoped to the caller's own tenant."""
    if not context.idempotency_key:
        raise ValueError("Idempotency-Key is required")
    store = _bound_store(context)
    record = await store.write(
        tenant=context.caller.tenant,
        key=params["key"],
        value=dict(params["value"]),
        idempotency_key=context.idempotency_key,
    )
    return {"value": record.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        build_read_op(
            op_id="memory.read",
            summary="Read one memory record scoped to the caller's own tenant",
            examples=("read this memory key",),
            params=MemoryReadParams,
            result=MemoryResult,
            handler="graph_os.api.ops.memory.handle_memory_read",
            scope="memory:read",
        ),
        build_write_op(
            op_id="memory.write",
            summary="Write one memory record scoped to the caller's own tenant",
            examples=("remember this for later",),
            params=MemoryWriteParams,
            result=MemoryResult,
            handler="graph_os.api.ops.memory.handle_memory_write",
            scope="memory:write",
        ),
    )


specs = operations

__all__ = [
    "MemoryReadParams",
    "MemoryRecord",
    "MemoryResult",
    "MemoryStore",
    "MemoryWriteParams",
    "handle_memory_read",
    "handle_memory_write",
    "operations",
    "specs",
]
