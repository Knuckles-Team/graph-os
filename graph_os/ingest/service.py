"""Typed ingestion-runner facade for the GraphOS operation registry.

GRAPHOS-OPS-R020.1: GraphOS exposes ingest operations for repository
indexing, source sync, pack and job management, drift listing and repair,
and embedding admission (GRAPHOS-OPS-R020.2 through R020.5), but calls the
ingestion SDK only through one typed runner port. An uncomposed runner fails
closed with a typed ``UNAVAILABLE`` refusal rather than an opaque error or a
silent no-op, matching the ``graph_os.access``/``graph_os.fleet`` service
layer convention: pure functions over the verified operation context, params,
and op spec, reusable across every surface the invoke pipeline serves.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke.pipeline import OperationRefused

IngestSyncMode = Literal["full", "incremental"]
IngestJobStatus = Literal["queued", "running", "completed", "failed"]


class IngestSyncReceipt(BaseModel):
    """A durable record of one accepted source-sync request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    source_id: str
    tenant: str
    mode: IngestSyncMode
    status: IngestJobStatus


@runtime_checkable
class IngestRunner(Protocol):
    """The one typed port GraphOS calls the ingestion SDK through.

    GRAPHOS-OPS-R020.2 through R020.5 add further port methods (repository
    indexing, pack/job management, drift repair, embedding admission) as
    their own slices land; this port starts with the one entry point
    GRAPHOS-OPS-R020.1 proves end to end.
    """

    async def sync_source(
        self, *, tenant: str, source_id: str, mode: str, idempotency_key: str
    ) -> IngestSyncReceipt: ...


def _bound_runner(context: Any) -> IngestRunner:
    runner = context.services.get("ingest_runner")
    if runner is None:
        raise OperationRefused(
            "UNAVAILABLE", {"reason": "ingest runner is not composed"}
        )
    return runner


async def sync_source(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Request a durable source sync through the composed ingestion runner."""
    if not context.idempotency_key:
        raise ValueError("Idempotency-Key is required")
    runner = _bound_runner(context)
    receipt = await runner.sync_source(
        tenant=context.caller.tenant,
        source_id=params["source_id"],
        mode=params["mode"],
        idempotency_key=context.idempotency_key,
    )
    return {"value": receipt.model_dump(mode="json")}
