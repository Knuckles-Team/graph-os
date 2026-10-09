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

from graph_os.api.ops._common import Params, build_tenant_read_op, handle_tenant_read
from graph_os.api.registry import OpSpec


class SwarmTopologyReadParams(Params):
    pass


class SwarmTopology(BaseModel):
    """The current, tenant-scoped swarm topology; the reader owns its shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    nodes: tuple[str, ...] = ()


class SwarmTopologyReadResult(Params):
    value: dict[str, Any]


@runtime_checkable
class SwarmTopologyReader(Protocol):
    """The one typed port GraphOS calls the swarm topology state through."""

    async def read(self, *, tenant: str) -> SwarmTopology: ...


async def handle_swarm_topology_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's swarm topology through the composed reader."""
    return await handle_tenant_read(
        context,
        service_name="swarm_topology_reader",
        unavailable_reason="swarm topology reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        build_tenant_read_op(
            op_id="swarm.topology.read",
            summary="Show this tenant's current swarm topology",
            examples=("show the current swarm topology",),
            params=SwarmTopologyReadParams,
            result=SwarmTopologyReadResult,
            handler="graph_os.api.ops.swarm.handle_swarm_topology_read",
            scope="swarm:read",
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
