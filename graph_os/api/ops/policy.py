"""Policy read operation (GRAPHOS-OPS-R021.3, policy slice).

``policy.read`` hands back the caller's own tenant's active policy state
from the composed ``policy_reader`` port. The OpSpec wiring, the
not-composed-yet refusal, and the trivial params/result pair all come from
:mod:`graph_os.api.ops._common`; this module only supplies the port
protocol, the state shape, and the service name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from graph_os.api.ops import _common
from graph_os.api.registry import OpSpec

PolicyReadParams, PolicyReadResult = _common.read_op_models("PolicyRead")
#: The active, tenant-scoped policy state; the reader owns its exact shape.
PolicyState = _common.tenant_state_model(
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
    return await _common.handle_tenant_read(
        context,
        service_name="policy_reader",
        unavailable_reason="policy reader is not composed",
    )


def operations() -> tuple[OpSpec, ...]:
    return (
        _common.build_tenant_read_op(
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
