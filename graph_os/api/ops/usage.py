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

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

UsageReadParams, UsageReadResult = _common.read_op_models("UsageRead")
#: A tenant-scoped usage snapshot; the reader owns its exact shape.
UsageSnapshot = _common.tenant_state_model(
    "UsageSnapshot",
    field_name="totals",
    field_type=dict[str, float],
    default_factory=dict,
)


@runtime_checkable
class UsageReader(Protocol):
    """The one typed port GraphOS calls the engine's usage method through."""

    async def read(self, *, tenant: str) -> UsageSnapshot: ...


async def handle_usage_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's usage snapshot through the composed reader."""
    return await _common.handle_tenant_read(
        context,
        service_name="usage_reader",
        unavailable_reason="usage reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        _common.build_tenant_read_op(
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
