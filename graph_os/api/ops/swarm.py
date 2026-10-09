"""Swarm topology read operation (GRAPHOS-OPS-R021.3, swarm slice).

Implements the swarm portion of GRAPHOS-OPS-R021: a single
``swarm.topology.read`` operation surfacing the current swarm topology to an
authorized caller through one typed port, matching the ingest-runner facade
convention in :mod:`graph_os.api.ops.ingest`. An uncomposed reader fails
closed with a typed ``UNAVAILABLE`` refusal rather than fabricating a
topology.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SwarmTopologyReadParams(_Params):
    pass


class SwarmTopology(BaseModel):
    """The current, tenant-scoped swarm topology; the reader owns its shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    nodes: tuple[str, ...] = ()


class SwarmTopologyReadResult(_Params):
    value: dict[str, Any]


@runtime_checkable
class SwarmTopologyReader(Protocol):
    """The one typed port GraphOS calls the swarm topology state through."""

    async def read(self, *, tenant: str) -> SwarmTopology: ...


def _bound_reader(context: Any) -> SwarmTopologyReader:
    reader = context.services.get("swarm_topology_reader")
    if reader is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "swarm topology reader is not composed"}
        )
    return reader


async def handle_swarm_topology_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's swarm topology through the composed reader."""
    reader = _bound_reader(context)
    topology = await reader.read(tenant=context.caller.tenant)
    return {"value": topology.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="swarm.topology.read",
            verb=Verb.ASK,
            summary="Show this tenant's current swarm topology",
            examples=("show the current swarm topology",),
            params=SwarmTopologyReadParams,
            result=SwarmTopologyReadResult,
            binding=Composite(
                handler="graph_os.api.ops.swarm.handle_swarm_topology_read"
            ),
            scopes=frozenset({"swarm:read"}),
            effect=Effect.READ,
        ),
    )


specs = operations

__all__ = [
    "SwarmTopology",
    "SwarmTopologyReadParams",
    "SwarmTopologyReadResult",
    "SwarmTopologyReader",
    "handle_swarm_topology_read",
    "operations",
    "specs",
]
