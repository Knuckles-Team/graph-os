"""Swarm planning is an EG assembly question under caller authority."""

from graph_os.api.registry import EgMethod, EgSchemaRef, Idempotency, OpSpec, Verb


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="swarm.plan",
            verb=Verb.ASK,
            summary="Prove a candidate agent topology without acquiring capacity.",
            examples=("Plan a bounded swarm for this task",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/AgentAssemble"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.storage.json#/methods/AgentAssemble"
            ),
            binding=EgMethod(service="AgentAssemble", op="AgentAssemble"),
            scopes=frozenset({"agent:assemble-read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
