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
    """Expose tenant-scoped derivation and CEP polling through EG."""
    return (
        OpSpec(
            id="telemetry.derive.run",
            verb=Verb.ACT,
            summary="Derive ontology-bound telemetry, anomaly and conformance facts.",
            examples=("Derive health facts for this time window",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/TelemetryDerive"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.ingestion.json#/methods/TelemetryDerive"
            ),
            binding=EgMethod(service="TelemetryDerive", op="TelemetryDerive"),
            scopes=frozenset({"telemetry:derive"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
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
