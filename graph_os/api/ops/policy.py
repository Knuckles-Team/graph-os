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

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.ops._common import Params, build_tenant_read_op, handle_tenant_read
from graph_os.api.registry import OpSpec


class PolicyReadParams(Params):
    pass


class PolicyState(BaseModel):
    """The active, tenant-scoped policy state; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    rules: dict[str, str] = Field(default_factory=dict)


class PolicyReadResult(Params):
    value: dict[str, Any]


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
