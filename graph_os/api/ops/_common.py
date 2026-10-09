"""Shared scaffolding for the tenant-scoped hosted-ops read/write modules.

Factors out the request-params base class, the "fail closed when a typed
port is not composed" refusal, and the single-port read-op OpSpec/handler
pairing duplicated across :mod:`graph_os.api.ops.policy`, `.telemetry`,
`.usage`, `.security`, `.swarm`, `.memory`, `.ops`, `.agents`, and `.work` --
each of which binds one or more typed ports to a tenant-scoped operation
through the ingest-runner facade convention (see
:mod:`graph_os.api.ops.ingest`). Behavior is unchanged: a module imports
these instead of repeating them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import Composite, Effect, OpSpec, Verb


class Params(BaseModel):
    """Base params model every ops module's request/result types extend."""

    model_config = ConfigDict(extra="forbid")


def bound_service(context: Any, name: str, *, reason: str) -> Any:
    """Return the named composed service, or fail closed with UNAVAILABLE."""
    service = context.services.get(name)
    if service is None:
        raise OperationRefused("UNAVAILABLE", {"reason": reason})
    return service


def build_tenant_read_op(
    *,
    op_id: str,
    summary: str,
    examples: tuple[str, ...],
    params: type[BaseModel],
    result: type[BaseModel],
    handler: str,
    scope: str,
) -> OpSpec:
    """The single-port ASK/READ OpSpec shared by the tenant-scoped readers."""
    return OpSpec(
        id=op_id,
        verb=Verb.ASK,
        summary=summary,
        examples=examples,
        params=params,
        result=result,
        binding=Composite(handler=handler),
        scopes=frozenset({scope}),
        effect=Effect.READ,
    )


async def handle_tenant_read(
    context: Any, *, service_name: str, unavailable_reason: str
) -> dict[str, Any]:
    """Return one tenant-scoped reader's state, shaped for the OpSpec result."""
    reader = bound_service(context, service_name, reason=unavailable_reason)
    state = await reader.read(tenant=context.caller.tenant)
    return {"value": state.model_dump(mode="json")}


__all__ = [
    "Params",
    "bound_service",
    "build_tenant_read_op",
    "handle_tenant_read",
]
