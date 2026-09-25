"""Telemetry operations backed by caller-scoped engine methods."""

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
    """Expose the CEP read path without granting subscription mutation."""
    return (
        OpSpec(
            id="telemetry.cep.poll",
            verb=Verb.ASK,
            summary="Poll an existing CEP subscription.",
            examples=("Show recent CEP matches for this subscription",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/CepPoll"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.messaging.json#/methods/CepPoll"
            ),
            binding=EgMethod(service="CepPoll", op="CepPoll"),
            scopes=frozenset({"cep:read"}),
            effect=Effect.READ,
            idempotency=Idempotency.NONE,
            audit=AuditClass.NONE,
        ),
    )
