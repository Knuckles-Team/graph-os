"""Ontology reads and validation over EG's schema authority."""

from graph_os.api.registry import EgMethod, EgSchemaRef, Idempotency, OpSpec, Verb


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="ontology.classes",
            verb=Verb.ASK,
            summary="Page through the caller graph's composed classes and properties.",
            examples=("List the ontology classes in this graph",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/GraphSchemaClasses"),
            result=EgSchemaRef(path="contract/schemas/result.reasoning.json#/methods/GraphSchemaClasses"),
            binding=EgMethod(service="GraphSchemaClasses", op="GraphSchemaClasses"),
            scopes=frozenset({"owl:read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="ontology.validate",
            verb=Verb.ASK,
            summary="Validate the caller graph against its composed SHACL shapes.",
            examples=("Validate this graph against its attached ontology",),
            params=EgSchemaRef(path="contract/schemas/method.request.json#/methods/ShaclValidate"),
            result=EgSchemaRef(path="contract/schemas/result.reasoning.json#/methods/ShaclValidate"),
            binding=EgMethod(service="ShaclValidate", op="ShaclValidate"),
            scopes=frozenset({"validation:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
