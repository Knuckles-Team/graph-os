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

from pydantic import BaseModel, ConfigDict, create_model

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import AuditClass, Composite, Effect, Idempotency, OpSpec, Verb


class Params(BaseModel):
    """Base params model every ops module's request/result types extend."""

    model_config = ConfigDict(extra="forbid")


def read_op_models(name: str) -> tuple[type[Params], type[Params]]:
    """A ``({name}Params, {name}Result)`` pair for one tenant-scoped read op.

    Every single-port reader module (``policy``, ``telemetry``, ``usage``,
    ``security``, ``swarm``) takes no request fields and returns the same
    ``{"value": dict[str, Any]}`` shape, so the pair is minted here instead
    of each module repeating an identical two-class block.
    """
    params = create_model(f"{name}Params", __base__=Params)
    result = create_model(f"{name}Result", __base__=Params, value=(dict[str, Any], ...))
    return params, result


def bound_service(context: Any, name: str, *, reason: str) -> Any:
    """Return the named composed service, or fail closed with UNAVAILABLE."""
    service = context.services.get(name)
    if service is None:
        raise OperationRefused("UNAVAILABLE", {"reason": reason})
    return service


def build_read_op(
    *,
    op_id: str,
    summary: str,
    examples: tuple[str, ...],
    params: type[BaseModel],
    result: type[BaseModel],
    handler: str,
    scope: str,
    verb: Verb = Verb.ASK,
) -> OpSpec:
    """The no-idempotency READ OpSpec shared by every tenant-scoped reader."""
    return OpSpec(
        id=op_id,
        verb=verb,
        summary=summary,
        examples=examples,
        params=params,
        result=result,
        binding=Composite(handler=handler),
        scopes=frozenset({scope}),
        effect=Effect.READ,
    )


# Retained for the single-reader modules that also use ``handle_tenant_read``.
build_tenant_read_op = build_read_op


def build_write_op(
    *,
    op_id: str,
    summary: str,
    examples: tuple[str, ...],
    params: type[BaseModel],
    result: type[BaseModel],
    handler: str,
    scope: str,
    verb: Verb = Verb.ACT,
    effect: Effect = Effect.WRITE,
) -> OpSpec:
    """The idempotency-required ACT/WRITE OpSpec shared by the store-backed writers."""
    return OpSpec(
        id=op_id,
        verb=verb,
        summary=summary,
        examples=examples,
        params=params,
        result=result,
        binding=Composite(handler=handler),
        scopes=frozenset({scope}),
        effect=effect,
        idempotency=Idempotency.KEY_REQUIRED,
        audit=AuditClass.EVENT,
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
    "build_read_op",
    "build_tenant_read_op",
    "build_write_op",
    "handle_tenant_read",
    "read_op_models",
]
