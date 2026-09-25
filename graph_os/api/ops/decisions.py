"""Caller-scoped DecisionLog read operations.

The EG DecisionLog wire method contains both reads and writes. Each handler
constructs one fixed read variant; callers cannot supply the variant or tenant.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    Composite,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListParams(_Params):
    after: str | None = None
    limit: int = Field(default=50, ge=1, le=256)


class RecordParams(_Params):
    record_id: str = Field(min_length=1)


class WindowParams(_Params):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(ge=0)


class AggregateParams(_Params):
    window: WindowParams
    question_id: str | None = None
    attribution: dict[str, Any] | None = None


async def _read(context: Any, op: Mapping[str, Any]) -> Any:
    from epistemic_graph.generated.coordination import send_decision_log

    # The generated public method accepts the transport held by the scoped
    # EpistemicGraphClient. BoundOperationRuntime has installed caller claims.
    result = await send_decision_log(context.client._client, {"op": dict(op)})
    return result.payload


async def list_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = ListParams.model_validate(params)
    return await _read(context, {
        "op": "list", "tenant_id": context.caller.tenant,
        "after": request.after, "limit": request.limit,
    })


async def get_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = RecordParams.model_validate(params)
    return await _read(context, {
        "op": "get", "tenant_id": context.caller.tenant,
        "record_id": request.record_id,
    })


async def provenance_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = RecordParams.model_validate(params)
    return await _read(context, {
        "op": "provenance", "tenant_id": context.caller.tenant,
        "record_id": request.record_id,
    })


async def aggregate_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = AggregateParams.model_validate(params)
    body = request.model_dump(exclude_none=True)
    body["tenant_id"] = context.caller.tenant
    return await _read(context, {"op": "aggregate", "request": body})


_RESULT = EgSchemaRef(path="contract/schemas/result.coordination.json#/methods/DecisionLog")


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="decisions.list",
            verb=Verb.ASK,
            summary="List bounded DecisionLog records visible to the caller.",
            examples=("Show my recent decisions",),
            params=ListParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.list_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decisions.get",
            verb=Verb.ASK,
            summary="Read a visible DecisionLog record by ID.",
            examples=("Show decision record d-123",),
            params=RecordParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.get_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decisions.provenance",
            verb=Verb.WHY,
            summary="Read a visible decision and its evaluations and resolutions.",
            examples=("Why did decision d-123 resolve this way?",),
            params=RecordParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.provenance_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decisions.aggregate",
            verb=Verb.ASK,
            summary="Read visibility-filtered DecisionLog outcome aggregates.",
            examples=("Summarize decisions during this window",),
            params=AggregateParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.aggregate_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
