"""Search and semantic index operations bound directly to EG."""

from graph_os.api.registry import (
    AuditClass,
    Effect,
    EgMethod,
    EgSchemaRef,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Verb,
)


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="search.semantic",
            verb=Verb.ASK,
            summary="Search the caller's semantic index by embedding.",
            examples=("Find semantically similar records for this embedding",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/SemanticSearch"),
            result=EgSchemaRef(path="contract/schemas/result.ingestion.json#/methods/SemanticSearch"),
            binding=EgMethod(service="SemanticSearch", op="SemanticSearch"),
            scopes=frozenset({"compute:semantic"}),
            principals=PrincipalRule.SERVICE_ONLY,
            effect=Effect.READ,
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="indexes.semantic.manage",
            verb=Verb.MANAGE,
            summary="Manage a durable semantic binding and its ingestion stages.",
            examples=("Admit a semantic binding for this source",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/SemanticIndex"),
            result=EgSchemaRef(path="contract/schemas/result.ingestion.json#/methods/SemanticIndex"),
            binding=EgMethod(service="SemanticIndex", op="SemanticIndex"),
            scopes=frozenset({"semantic:binding-write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
    )
