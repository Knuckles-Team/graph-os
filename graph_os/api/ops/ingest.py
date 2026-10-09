"""Ingest operations for the GraphOS intent registry (GRAPHOS-OPS-R020.1).

GRAPHOS-OPS-R020 exposes ingest operations for repository indexing, source
sync, pack and job management, drift listing and repair, and embedding
admission, calling the ingestion SDK only through a typed runner facade
(:mod:`graph_os.ingest.service`). This slice declares the typed port and its
one entry point, ``ingest.sources.sync``: the acceptance case GraphOS proves
first is a durable job receipt on success and a typed ``UNAVAILABLE``
refusal when no runner is composed. The remaining operation family
(``ingest.repositories.index``, ``ingest.sources.{list,status}``,
``ingest.packs.*``, ``ingest.jobs.*``, ``ingest.drift.*``,
``ingest.embedding.*``) is tracked as GRAPHOS-OPS-R020.2 through R020.5 in
``specs/hosted-api-operations/``.
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
from graph_os.ingest.service import sync_source

#: Re-exported so ``registry_factory.get_registry()`` can resolve the
#: ``Composite(handler="graph_os.api.ops.ingest.sync_source")`` binding from
#: this module's own namespace, matching the ``access`` ops-module
#: convention even though the implementation lives in the service layer
#: (``graph_os.ingest.service``).
__all__ = ["operations", "sync_source"]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestSourceSyncParams(_Params):
    source_id: str = Field(min_length=1, max_length=256)
    mode: str = Field(default="incremental", pattern=r"^(full|incremental)$")


class IngestSyncResult(_Params):
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
    )
