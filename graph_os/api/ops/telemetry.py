"""Telemetry read operation (GRAPHOS-OPS-R024.1).

``telemetry.read`` is the engine's own tenant-scoped telemetry method,
reached through the composed ``telemetry_reader`` port -- never a snapshot
GraphOS fabricates locally. See :mod:`graph_os.api.ops._common` for the
OpSpec, refusal, and params/result scaffolding this module reuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

TelemetryReadParams, TelemetryReadResult = _common.read_op_models("TelemetryRead")
#: A tenant-scoped telemetry snapshot; the reader owns its exact shape.
TelemetrySnapshot = _common.tenant_state_model(
    "TelemetrySnapshot",
    field_name="metrics",
    field_type=dict[str, float],
    default_factory=dict,
)


@runtime_checkable
class TelemetryReader(Protocol):
    """The one typed port GraphOS calls the engine's telemetry method through."""

    async def read(self, *, tenant: str) -> TelemetrySnapshot: ...


async def handle_telemetry_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's telemetry snapshot through the composed reader."""
    return await _common.handle_tenant_read(
        context,
        service_name="telemetry_reader",
        unavailable_reason="telemetry reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        _common.build_tenant_read_op(
            op_id="telemetry.read",
            summary="Show this tenant's telemetry snapshot",
            examples=("show telemetry for this tenant",),
            params=TelemetryReadParams,
            result=TelemetryReadResult,
            handler="graph_os.api.ops.telemetry.handle_telemetry_read",
            scope="telemetry:read",
        ),
    )


specs = operations

__all__ = [
    "TelemetryReadParams",
    "TelemetryReadResult",
    "TelemetryReader",
    "TelemetrySnapshot",
    "handle_telemetry_read",
    "operations",
    "specs",
]
