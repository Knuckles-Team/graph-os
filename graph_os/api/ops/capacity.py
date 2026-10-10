"""Hosted capacity-throttle status and mode operations.

GRAPHOS-CAPACITY-R002 (status/mode half): projects the in-process
:class:`~graph_os.fleet.throttle_service.ThrottleRegistry` through the same
operation registry, identity and error-handling chokepoint every other
GraphOS operation uses (see :mod:`graph_os.api.ops.browser` for the sibling
pattern). ``capacity.throttle.status`` declares only ``capacity:read``;
``capacity.throttle.set_mode`` declares ``capacity:admin`` and
``Effect.ADMIN`` -- the shared invoke pipeline enforces the declared scope
and the resulting console step-up before either handler runs, so a reader
can never flip enforcement through this operation.

Updating the engine's own ``CapacityCell`` ceiling is not implemented here:
the pinned engine contract does not yet publish a capacity method
(confirmed empty in ``epistemic_graph``'s ``contract/methods.json`` at the
time of writing), and ``specs/adaptive-capacity/plan.md`` requires that
exact method and schema to be pinned before this feature advertises it.
Until then, a caller sees the GraphOS-owned throttle state only; this
operation never fabricates an engine ceiling.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from graph_os.api.ops._simple_op import OpSpecArgs, build_operations
from graph_os.api.registry import (
    AuditClass,
    Effect,
    OpSpec,
    Surface,
    Verb,
)
from graph_os.fleet.error_budget import Partition, ThrottleMode


class CapacityPartitionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str
    child: str
    operation_class: str
    policy_revision: str


class CapacitySetModeParams(CapacityPartitionParams):
    mode: ThrottleMode


class CapacityStatusResult(BaseModel):
    mode: str
    current_limit: int | None
    reason: str


class CapacityModeResult(BaseModel):
    mode: str


def _partition(params: Mapping[str, Any]) -> Partition:
    return Partition(
        tenant=params["tenant"],
        child=params["child"],
        operation_class=params["operation_class"],
        policy_revision=params["policy_revision"],
    )


def _bound_registry(context: Any) -> Any:
    registry = context.services.get("throttle_registry")
    if registry is None:
        raise RuntimeError("capacity throttle controller is not bound")
    return registry


async def handle_capacity_status(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Report the partition's recorded throttle state, never a fabricated one."""
    status = _bound_registry(context).status(_partition(params))
    return {
        "value": {
            "mode": status.mode.value,
            "current_limit": status.current_limit,
            "reason": status.reason,
        }
    }


async def handle_capacity_set_mode(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Select observe or enforce mode; the invoke pipeline already verified authority."""
    registry = _bound_registry(context)
    partition = _partition(params)
    registry.set_mode(partition, ThrottleMode(params["mode"]))
    return {"value": {"mode": registry.mode(partition).value}}


def operations() -> tuple[OpSpec, ...]:
    return build_operations(
        OpSpecArgs(
            "capacity.throttle.status",
            Verb.ASK,
            "Show this partition's current throttle mode and limit",
            ("show capacity throttle status for this child",),
            CapacityPartitionParams,
            CapacityStatusResult,
            "graph_os.api.ops.capacity.handle_capacity_status",
            frozenset({"capacity:read"}),
            Effect.READ,
            AuditClass.NONE,
            frozenset({Surface.MCP, Surface.HTTP, Surface.A2A, Surface.CONSOLE}),
        ),
        OpSpecArgs(
            "capacity.throttle.set_mode",
            Verb.MANAGE,
            "Select observe or enforce throttle mode for this partition",
            ("switch this child to enforce mode",),
            CapacitySetModeParams,
            CapacityModeResult,
            "graph_os.api.ops.capacity.handle_capacity_set_mode",
            frozenset({"capacity:admin"}),
            Effect.ADMIN,
            AuditClass.EVENT,
            frozenset({Surface.MCP, Surface.HTTP, Surface.CONSOLE}),
        ),
    )


specs = operations

__all__ = [
    "CapacityModeResult",
    "CapacityPartitionParams",
    "CapacitySetModeParams",
    "CapacityStatusResult",
    "handle_capacity_set_mode",
    "handle_capacity_status",
    "operations",
    "specs",
]
