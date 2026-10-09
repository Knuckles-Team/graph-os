"""Typed ingestion-runner facade for the GraphOS operation registry.

GRAPHOS-OPS-R020.1/R020.2: GraphOS exposes ingest operations for repository
indexing, source sync, pack and job management, drift listing and repair,
and embedding admission (GRAPHOS-OPS-R020.3 through R020.5 remain), but
calls the ingestion SDK only through one typed runner port. An uncomposed
runner fails closed with a typed ``UNAVAILABLE`` refusal rather than an
opaque error or a silent no-op, matching the ``graph_os.access``/
``graph_os.fleet`` service layer convention: pure functions over the
verified operation context, params, and op spec, reusable across every
surface the invoke pipeline serves.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from graph_os.api.invoke.pipeline import OperationRefused

IngestSyncMode = Literal["full", "incremental"]
IngestJobStatus = Literal["queued", "running", "completed", "failed"]
IngestSourceState = Literal["active", "paused", "error"]


class IngestSyncReceipt(BaseModel):
    """A durable record of one accepted source-sync request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    source_id: str
    tenant: str
    mode: IngestSyncMode
    status: IngestJobStatus


class IngestIndexReceipt(BaseModel):
    """A durable record of one accepted repository-index request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    repo_id: str
    tenant: str
    status: IngestJobStatus


class IngestSourceRecord(BaseModel):
    """One ingestion source's identity and current state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    tenant: str
    state: IngestSourceState


class IngestSourceInventory(BaseModel):
    """A tenant's full ingestion-source inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[IngestSourceRecord, ...]


@runtime_checkable
class IngestRunner(Protocol):
    """The one typed port GraphOS calls the ingestion SDK through.

    GRAPHOS-OPS-R020.3 through R020.5 add further port methods (pack/job
    management, drift repair, embedding admission) as their own slices land;
    this port now covers the R020.1 source-sync entry point and the R020.2
    repository-indexing and source-inventory entry points.
    """

    async def sync_source(
        self, *, tenant: str, source_id: str, mode: str, idempotency_key: str
    ) -> IngestSyncReceipt: ...

    async def index_repository(
        self, *, tenant: str, repo_id: str, idempotency_key: str
    ) -> IngestIndexReceipt: ...

    async def list_sources(self, *, tenant: str) -> IngestSourceInventory: ...

    async def get_source_status(
        self, *, tenant: str, source_id: str
    ) -> IngestSourceRecord: ...


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


async def index_repository(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Request a durable repository index through the composed ingestion runner."""
    if not context.idempotency_key:
        raise ValueError("Idempotency-Key is required")
    runner = _bound_runner(context)
    repo_id = params["repo_id"]
    receipt = await runner.index_repository(
        tenant=context.caller.tenant,
        repo_id=repo_id,
        idempotency_key=context.idempotency_key,
    )
    return {"value": receipt.model_dump(mode="json")}


async def list_sources(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """List this tenant's ingestion sources through the composed runner."""
    runner = _bound_runner(context)
    inventory = await runner.list_sources(tenant=context.caller.tenant)
    return {"value": inventory.model_dump(mode="json")}


async def get_source_status(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Show one ingestion source's current status through the composed runner."""
    runner = _bound_runner(context)
    record = await runner.get_source_status(
        tenant=context.caller.tenant, source_id=params["source_id"]
    )
    return {"value": record.model_dump(mode="json")}
