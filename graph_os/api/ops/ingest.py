"""Ingest operations for the GraphOS intent registry (GRAPHOS-OPS-R020.1, R020.2).

GRAPHOS-OPS-R020 exposes ingest operations for repository indexing, source
sync, pack and job management, drift listing and repair, and embedding
admission, calling the ingestion SDK only through a typed runner facade
(:mod:`graph_os.ingest.service`). R020.1 proved the port's first entry point,
``ingest.sources.sync``: a durable job receipt on success and a typed
``UNAVAILABLE`` refusal when no runner is composed. This slice (R020.2) adds
``ingest.repositories.index`` and ``ingest.sources.{list,status}`` through
the same port. The remaining operation family (``ingest.packs.*``,
``ingest.jobs.*``, ``ingest.drift.*``, ``ingest.embedding.*``) is tracked as
GRAPHOS-OPS-R020.3 through R020.5 in ``specs/hosted-api-operations/``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Verb,
)
from graph_os.ingest.service import (
    get_source_status,
    index_repository,
    list_sources,
    sync_source,
)

#: Re-exported so ``registry_factory.get_registry()`` can resolve each
#: ``Composite(handler="graph_os.api.ops.ingest.<name>")`` binding from this
#: module's own namespace, matching the ``access`` ops-module convention even
#: though the implementation lives in the service layer
#: (``graph_os.ingest.service``).
__all__ = [
    "operations",
    "get_source_status",
    "index_repository",
    "list_sources",
    "sync_source",
]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestSourceSyncParams(_Params):
    source_id: str = Field(min_length=1, max_length=256)
    mode: str = Field(default="incremental", pattern=r"^(full|incremental)$")


class IngestSyncResult(_Params):
    value: dict[str, str]


class IngestRepositoryIndexParams(_Params):
    repo_id: str = Field(min_length=1, max_length=256)


class IngestIndexResult(_Params):
    value: dict[str, str]


class IngestSourcesListParams(_Params):
    pass


class IngestSourceListResult(_Params):
    value: dict[str, object]


class IngestSourceStatusParams(_Params):
    source_id: str = Field(min_length=1, max_length=256)


class IngestSourceStatusResult(_Params):
    value: dict[str, str]


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="ingest.sources.sync",
            verb=Verb.ACT,
            summary="Request a durable sync of one ingestion source",
            examples=("sync this source now",),
            params=IngestSourceSyncParams,
            result=IngestSyncResult,
            binding=Composite(handler="graph_os.api.ops.ingest.sync_source"),
            scopes=frozenset({"ingest:write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="ingest.repositories.index",
            verb=Verb.ACT,
            summary="Request a durable index of one repository",
            examples=("index this repository now",),
            params=IngestRepositoryIndexParams,
            result=IngestIndexResult,
            binding=Composite(handler="graph_os.api.ops.ingest.index_repository"),
            scopes=frozenset({"ingest:write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="ingest.sources.list",
            verb=Verb.FIND,
            summary="List this tenant's ingestion sources",
            examples=("list my ingestion sources",),
            params=IngestSourcesListParams,
            result=IngestSourceListResult,
            binding=Composite(handler="graph_os.api.ops.ingest.list_sources"),
            scopes=frozenset({"ingest:read"}),
            effect=Effect.READ,
        ),
        OpSpec(
            id="ingest.sources.status",
            verb=Verb.ASK,
            summary="Show one ingestion source's current status",
            examples=("show the status of this ingestion source",),
            params=IngestSourceStatusParams,
            result=IngestSourceStatusResult,
            binding=Composite(handler="graph_os.api.ops.ingest.get_source_status"),
            scopes=frozenset({"ingest:read"}),
            effect=Effect.READ,
        ),
    )
