"""Curated query operations over the caller's EG authority."""

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    Verb,
)


class SqlSchemaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str | None = Field(default=None, alias="schema")


class SqlSchemaResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    catalogs: list[dict[str, Any]]
    capabilities: dict[str, bool]
    counts: dict[str, int]


async def sql_schema_handler(context: Any, params: Mapping[str, Any], op: OpSpec) -> dict[str, Any]:
    """Project the SQL catalog using the invoke layer's caller-bound EG client."""
    from graph_os.gateway.sql_catalog import sql_schema

    request = SqlSchemaParams.model_validate(params)
    return await sql_schema(context.client, schema=request.schema_name)


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
            id="query.sql_schema",
            verb=Verb.FIND,
            summary="Inspect readable SQL catalogs, tables, and columns.",
            examples=("Show the SQL tables available in this graph",),
            params=SqlSchemaParams,
            result=SqlSchemaResult,
            binding=Composite(handler="graph_os.api.ops.query.sql_schema_handler"),
            scopes=frozenset({"query:sql"}),
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
