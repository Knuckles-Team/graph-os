"""Usage read operation (GRAPHOS-OPS-R024.3).

``usage.read`` sources its numbers from the engine's own ``usage_reader``
port, not a counter GraphOS keeps on the side. The OpSpec/refusal/params
plumbing lives in :mod:`graph_os.api.ops._common`; this module owns only
the port protocol and the per-tenant totals shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

UsageReadParams, UsageReadResult = _common.read_op_models("UsageRead")


class UsageSnapshot(_common.TenantState):
    """A tenant-scoped usage snapshot; the reader owns its exact shape."""

    totals: dict[str, float] = Field(default_factory=dict)


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
