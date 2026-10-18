"""Telemetry read operation (ARBORA-OPS-R024.1).

``telemetry.read`` is the engine's own tenant-scoped telemetry method,
reached through the composed ``telemetry_reader`` port -- never a snapshot
Arbora fabricates locally. See :mod:`arbora.api.ops._common` for the
OpSpec, refusal, and params/result scaffolding this module reuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from arbora.api.ops import _common
from arbora.api.registry import OpSpec

TelemetryReadParams, TelemetryReadResult = _common.read_op_models("TelemetryRead")


class TelemetrySnapshot(_common.TenantState):
    """A tenant-scoped telemetry snapshot; the reader owns its exact shape."""

    metrics: dict[str, float] = Field(default_factory=dict)


@runtime_checkable
class TelemetryReader(Protocol):
    """The one typed port Arbora calls the engine's telemetry method through."""

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
            handler="arbora.api.ops.telemetry.handle_telemetry_read",
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
