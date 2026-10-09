"""Telemetry read operation (GRAPHOS-OPS-R024.1).

Implements the telemetry portion of GRAPHOS-OPS-R024: a single
``telemetry.read`` operation bound to the engine's tenant-scoped telemetry
method through one typed port, matching the ingest-runner-facade convention
in :mod:`graph_os.api.ops.ingest`. An uncomposed reader fails closed with a
typed ``UNAVAILABLE`` refusal rather than fabricating a snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from graph_os.api.ops._common import (
    build_tenant_read_op,
    handle_tenant_read,
    read_op_models,
    tenant_state_model,
)
from graph_os.api.registry import OpSpec

TelemetryReadParams, TelemetryReadResult = read_op_models("TelemetryRead")
#: A tenant-scoped telemetry snapshot; the reader owns its exact shape.
TelemetrySnapshot = tenant_state_model(
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
    return await handle_tenant_read(
        context,
        service_name="telemetry_reader",
        unavailable_reason="telemetry reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        build_tenant_read_op(
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
