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

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageReadParams(_Params):
    pass


class UsageSnapshot(BaseModel):
    """A tenant-scoped usage snapshot; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    totals: dict[str, float] = Field(default_factory=dict)


class UsageReadResult(_Params):
    value: dict[str, Any]


@runtime_checkable
class UsageReader(Protocol):
    """The one typed port GraphOS calls the engine's usage method through."""

    async def read(self, *, tenant: str) -> UsageSnapshot: ...


def _bound_reader(context: Any) -> UsageReader:
    reader = context.services.get("usage_reader")
    if reader is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "usage reader is not composed"}
        )
    return reader


async def handle_usage_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's usage snapshot through the composed reader."""
    reader = _bound_reader(context)
    snapshot = await reader.read(tenant=context.caller.tenant)
    return {"value": snapshot.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="usage.read",
            verb=Verb.ASK,
            summary="Show this tenant's engine-sourced usage data",
            examples=("show usage for this tenant",),
            params=UsageReadParams,
            result=UsageReadResult,
            binding=Composite(handler="graph_os.api.ops.usage.handle_usage_read"),
            scopes=frozenset({"usage:read"}),
            effect=Effect.READ,
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
