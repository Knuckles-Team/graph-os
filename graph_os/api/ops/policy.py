"""Policy read operation (GRAPHOS-OPS-R021.3, policy slice).

Implements the policy portion of GRAPHOS-OPS-R021: a single ``policy.read``
operation surfacing the active policy state to an authorized caller through
one typed port, matching the ingest-runner facade convention in
:mod:`graph_os.api.ops.ingest`. An uncomposed reader fails closed with a
typed ``UNAVAILABLE`` refusal rather than fabricating a policy state.
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

PolicyReadParams, PolicyReadResult = read_op_models("PolicyRead")
#: The active, tenant-scoped policy state; the reader owns its exact shape.
PolicyState = tenant_state_model(
    "PolicyState", field_name="rules", field_type=dict[str, str], default_factory=dict
)


@runtime_checkable
class PolicyReader(Protocol):
    """The one typed port GraphOS calls the active policy state through."""

    async def read(self, *, tenant: str) -> PolicyState: ...


async def handle_policy_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's active policy state through the composed reader."""
    return await handle_tenant_read(
        context,
        service_name="policy_reader",
        unavailable_reason="policy reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        build_tenant_read_op(
            op_id="policy.read",
            summary="Show this tenant's active policy state",
            examples=("show the active policy state",),
            params=PolicyReadParams,
            result=PolicyReadResult,
            handler="graph_os.api.ops.policy.handle_policy_read",
            scope="policy:read",
        ),
    )


specs = operations

__all__ = [
    "PolicyReadParams",
    "PolicyReadResult",
    "PolicyReader",
    "PolicyState",
    "handle_policy_read",
    "operations",
    "specs",
]
