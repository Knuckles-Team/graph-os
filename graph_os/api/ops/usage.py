"""Usage read operation (GRAPHOS-OPS-R024.3).

Implements the usage portion of GRAPHOS-OPS-R024: a single ``usage.read``
operation sourcing usage data from the engine, through one typed port,
rather than a local counter, matching the ingest-runner facade convention
in :mod:`graph_os.api.ops.ingest`. An uncomposed reader fails closed with a
typed ``UNAVAILABLE`` refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.ops._common import build_tenant_read_op, handle_tenant_read, read_op_models
from graph_os.api.registry import OpSpec

UsageReadParams, UsageReadResult = read_op_models("UsageRead")


class UsageSnapshot(BaseModel):
    """A tenant-scoped usage snapshot; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    totals: dict[str, float] = Field(default_factory=dict)


@runtime_checkable
class UsageReader(Protocol):
    """The one typed port GraphOS calls the engine's usage method through."""

    async def read(self, *, tenant: str) -> UsageSnapshot: ...


async def handle_usage_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's usage snapshot through the composed reader."""
    return await handle_tenant_read(
        context,
        service_name="usage_reader",
        unavailable_reason="usage reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        build_tenant_read_op(
            op_id="usage.read",
            summary="Show this tenant's engine-sourced usage data",
            examples=("show usage for this tenant",),
            params=UsageReadParams,
            result=UsageReadResult,
            handler="graph_os.api.ops.usage.handle_usage_read",
            scope="usage:read",
        ),
    )


specs = operations

__all__ = [
    "UsageReadParams",
    "UsageReadResult",
    "UsageReader",
    "UsageSnapshot",
    "handle_usage_read",
    "operations",
    "specs",
]
