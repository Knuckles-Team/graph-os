"""Telemetry operations backed by caller-scoped engine methods."""

from collections.abc import Mapping
from typing import Any

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


class FactPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=50, ge=1, le=200)


class FactPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[dict[str, Any]]
    next_cursor: str | None


_FACT_LABELS = {
    "telemetry.anomalies": "HealthAnomaly",
    "telemetry.conformance": "ConformanceViolation",
}


async def facts_page_handler(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Page only the selected fact class through the verified caller's EG client."""
    request = FactPageParams.model_validate(params)
    label = _FACT_LABELS[op.id]
    rows = await context.client.nodes.list_by_label(
        label, request.limit, after=request.cursor
    )
    items = [{"id": node_id, "properties": properties} for node_id, properties in rows]
    return {
        "items": items,
        "next_cursor": rows[-1][0] if len(rows) == request.limit else None,
    }


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
        OpSpec(
            id="telemetry.anomalies",
            verb=Verb.ASK,
            summary="Page derived health anomaly facts in this graph.",
            examples=("Show recent health anomaly facts",),
            params=FactPageParams,
            result=FactPageResult,
            binding=Composite(handler="graph_os.api.ops.telemetry.facts_page_handler"),
            scopes=frozenset({"cep:read", "node:read"}),
            idempotency=Idempotency.NATURAL,
        ),
        OpSpec(
            id="telemetry.conformance",
            verb=Verb.ASK,
            summary="Page derived conformance violation facts in this graph.",
            examples=("Show service conformance violations",),
            params=FactPageParams,
            result=FactPageResult,
            binding=Composite(handler="graph_os.api.ops.telemetry.facts_page_handler"),
            scopes=frozenset({"cep:read", "node:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
