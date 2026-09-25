"""Stateful derived series operations; analytic reads use query.uql."""

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
            id="analytics.series.define",
            verb=Verb.WRITE,
            summary="Define a materialised series derived from a source series.",
            examples=("Define a rolling z-score series from this source",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/TsDefineSeries"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.storage.json#/methods/TsDefineSeries"
            ),
            binding=EgMethod(service="TsDefineSeries", op="TsDefineSeries"),
            scopes=frozenset({"timeseries:write"}),
            effect=Effect.WRITE,
            principals=PrincipalRule.SERVICE_ONLY,
            idempotency=Idempotency.NATURAL,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="analytics.series.drop",
            verb=Verb.WRITE,
            summary="Delete a derived series and all of its stored chunks.",
            examples=("Drop this obsolete derived series",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/TsDeleteSeries"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.storage.json#/methods/TsDeleteSeries"
            ),
            binding=EgMethod(service="TsDeleteSeries", op="TsDeleteSeries"),
            scopes=frozenset({"timeseries:write"}),
            effect=Effect.DESTRUCTIVE,
            principals=PrincipalRule.SERVICE_ONLY,
            idempotency=Idempotency.NATURAL,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="analytics.series.list",
            verb=Verb.ASK,
            summary="List the caller's series in the current graph.",
            examples=("Which time series are defined for this graph?",),
            params=EgSchemaRef(
                path="contract/schemas/method.request.json#/methods/TsListSeries"
            ),
            result=EgSchemaRef(
                path="contract/schemas/result.storage.json#/methods/TsListSeries"
            ),
            binding=EgMethod(service="TsListSeries", op="TsListSeries"),
            scopes=frozenset({"timeseries:read"}),
            idempotency=Idempotency.NATURAL,
        ),
    )
