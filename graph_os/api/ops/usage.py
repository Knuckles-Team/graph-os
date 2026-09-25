"""Engine-owned resource usage; session analytics await the EG fact contract."""

from graph_os.api.registry import EgMethod, EgSchemaRef, Idempotency, OpSpec, Verb


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="usage.resources",
            verb=Verb.ASK,
            summary="Read a bounded page of engine resource usage.",
            examples=("Show resource usage for graphs I can access",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/ResourceStatsPage"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.coordination.json#/methods/ResourceStatsPage"
            ),
            binding=EgMethod(service="ResourceStatsPage", op="ResourceStatsPage"),
            scopes=frozenset({"service:control"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
