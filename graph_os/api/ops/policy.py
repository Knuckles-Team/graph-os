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

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyReadParams(_Params):
    pass


class PolicyState(BaseModel):
    """The active, tenant-scoped policy state; the reader owns its exact shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    rules: dict[str, str] = Field(default_factory=dict)


class PolicyReadResult(_Params):
    value: dict[str, Any]


@runtime_checkable
class PolicyReader(Protocol):
    """The one typed port GraphOS calls the active policy state through."""

    async def read(self, *, tenant: str) -> PolicyState: ...


def _bound_reader(context: Any) -> PolicyReader:
    reader = context.services.get("policy_reader")
    if reader is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "policy reader is not composed"}
        )
    return reader


async def handle_policy_read(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return this tenant's active policy state through the composed reader."""
    reader = _bound_reader(context)
    state = await reader.read(tenant=context.caller.tenant)
    return {"value": state.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="policy.read",
            verb=Verb.ASK,
            summary="Show this tenant's active policy state",
            examples=("show the active policy state",),
            params=PolicyReadParams,
            result=PolicyReadResult,
            binding=Composite(handler="graph_os.api.ops.policy.handle_policy_read"),
            scopes=frozenset({"policy:read"}),
            effect=Effect.READ,
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
