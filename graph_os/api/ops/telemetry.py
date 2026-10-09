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

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelemetryReadParams(_Params):
    pass


class TelemetrySnapshot(BaseModel):
    """A tenant-scoped telemetry snapshot; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    metrics: dict[str, float] = Field(default_factory=dict)


class TelemetryReadResult(_Params):
    value: dict[str, Any]


@runtime_checkable
class TelemetryReader(Protocol):
    """The one typed port GraphOS calls the engine's telemetry method through."""

    async def read(self, *, tenant: str) -> TelemetrySnapshot: ...


def _bound_reader(context: Any) -> TelemetryReader:
    reader = context.services.get("telemetry_reader")
    if reader is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "telemetry reader is not composed"}
        )
    return reader


async def handle_telemetry_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's telemetry snapshot through the composed reader."""
    reader = _bound_reader(context)
    snapshot = await reader.read(tenant=context.caller.tenant)
    return {"value": snapshot.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="telemetry.read",
            verb=Verb.ASK,
            summary="Show this tenant's telemetry snapshot",
            examples=("show telemetry for this tenant",),
            params=TelemetryReadParams,
            result=TelemetryReadResult,
            binding=Composite(
                handler="graph_os.api.ops.telemetry.handle_telemetry_read"
            ),
            scopes=frozenset({"telemetry:read"}),
            effect=Effect.READ,
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
