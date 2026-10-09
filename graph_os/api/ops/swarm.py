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

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

SwarmTopologyReadParams, SwarmTopologyReadResult = _common.read_op_models(
    "SwarmTopologyRead"
)
#: The current, tenant-scoped swarm topology; the reader owns its shape.
SwarmTopology = _common.tenant_state_model(
    "SwarmTopology", field_name="nodes", field_type=tuple[str, ...], default=()
)


@runtime_checkable
class SwarmTopologyReader(Protocol):
    """The one typed port GraphOS calls the swarm topology state through."""

    async def read(self, *, tenant: str) -> SwarmTopology: ...


async def handle_swarm_topology_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's swarm topology through the composed reader."""
    return await _common.handle_tenant_read(
        context,
        service_name="swarm_topology_reader",
        unavailable_reason="swarm topology reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        _common.build_tenant_read_op(
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
