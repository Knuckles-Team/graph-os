"""Caller-scoped DecisionLog read operations.

The EG DecisionLog wire method contains both reads and writes. Each handler
constructs one fixed read variant; callers cannot supply the variant or tenant.
The public EG contract currently exposes Get and Aggregate as reads. A list or
provenance operation must be added at that authority before it can be served.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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


class RecordParams(_Params):
    record_id: str = Field(min_length=1, max_length=256)


class WindowParams(_Params):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(ge=0)


class AggregateParams(_Params):
    window: WindowParams
    question_id: str | None = Field(default=None, min_length=1, max_length=256)


async def _read(context: Any, op: Mapping[str, Any]) -> Any:
    from epistemic_graph.generated.coordination import send_decision_log

    # The generated public method accepts the transport held by the scoped
    # EpistemicGraphClient. BoundOperationRuntime has installed caller claims.
    result = await send_decision_log(context.client._client, {"op": dict(op)})
    return result.payload


async def get_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = RecordParams.model_validate(params)
    return await _read(
        context,
        {
            "op": "get",
            "tenant_id": context.caller.tenant,
            "record_id": request.record_id,
        },
    )


async def aggregate_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> Any:
    request = AggregateParams.model_validate(params)
    if request.window.from_ms > request.window.to_ms:
        raise ValueError("decision aggregate window must have from_ms <= to_ms")
    body = request.model_dump(exclude_none=True)
    body["tenant_id"] = context.caller.tenant
    return await _read(context, {"op": "aggregate", "request": body})


_RESULT = EgSchemaRef(
    path="contract/schemas/result.coordination.json#/methods/DecisionLog"
)


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="decisions.get",
            verb=Verb.ASK,
            summary="Read a statistical DecisionLog entry by ID.",
            examples=("Show statistical decision entry d-123",),
            params=RecordParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.get_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="decisions.aggregate",
            verb=Verb.ASK,
            summary="Read tenant-scoped statistical DecisionLog outcome aggregates.",
            examples=("Summarize decisions during this window",),
            params=AggregateParams,
            result=_RESULT,
            binding=Composite(handler="graph_os.api.ops.decisions.aggregate_handler"),
            scopes=frozenset({"agent:decision-read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
