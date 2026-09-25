"""Curated query operations over the caller's EG authority."""

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
            id="query.uql",
            verb=Verb.ASK,
            summary="Run a unified graph query under the caller's scope.",
            examples=("Find the graph nodes related to this source",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/Uql"),
            result=EgSchemaRef(path="contract/schemas/result.query.json#/methods/Uql"),
            binding=EgMethod(service="Uql", op="Uql"),
            scopes=frozenset({"query:unified"}),
            effect=Effect.READ,
            idempotency=Idempotency.NATURAL,
            audit=AuditClass.NONE,
        ),
        OpSpec(
            id="query.sparql",
            verb=Verb.ASK,
            summary="Read graph triples using SPARQL SELECT.",
            examples=("Which resources have this RDF type?",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/Sparql"),
            result=EgSchemaRef(path="contract/schemas/result.reasoning.json#/methods/Sparql"),
            binding=EgMethod(service="Sparql", op="Sparql"),
            scopes=frozenset({"sparql:read"}),
            effect=Effect.READ,
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="query.sql",
            verb=Verb.WRITE,
            summary="Execute SQL under the caller's SQL authority.",
            examples=("Create a governed SQL view for this graph",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/Sql"),
            result=EgSchemaRef(path="contract/schemas/result.query.json#/methods/Sql"),
            binding=EgMethod(service="Sql", op="Sql"),
            scopes=frozenset({"query:sql"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
    )
