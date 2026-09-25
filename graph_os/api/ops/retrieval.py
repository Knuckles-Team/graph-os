"""Retrieval evaluation and context operations backed by EG methods."""

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="retrieval.evaluate",
            verb=Verb.ACT,
            summary="Evaluate retrieval traces and optionally materialize a report.",
            examples=("Measure precision, recall and MRR for these traces",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/MineRetrievalQuality"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.compute.json#/methods/MineRetrievalQuality"
            ),
            binding=EgMethod(service="MineRetrievalQuality", op="MineRetrievalQuality"),
            scopes=frozenset({"mining:write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="context.view",
            verb=Verb.ASK,
            summary="Read a bounded context view for an agent.",
            examples=("Show this agent's context within a token budget",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/GetContextView"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.query.json#/methods/GetContextView"
            ),
            binding=EgMethod(service="GetContextView", op="GetContextView"),
            scopes=frozenset({"node:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
