"""Retrieval read operations (GRAPHOS-OPS-R021.2.1, retrieval slice).

``retrieval.search`` runs a context-budgeted retrieval through one typed port
under the caller's own tenant and authority; there is no local cache
substitute. An uncomposed retriever fails closed with a typed ``UNAVAILABLE``
refusal, matching :mod:`graph_os.api.ops.ingest` and
:mod:`graph_os.api.ops.memory`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.ops._common import Params, bound_service, build_read_op
from graph_os.api.registry import OpSpec, Verb


class RetrievalSearchParams(Params):
    query: str = Field(min_length=1, max_length=4096)
    context_budget: int = Field(default=4096, ge=1, le=1_000_000)


class RetrievalHit(BaseModel):
    """One retrieved passage with its provenance reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    text: str
    tokens: int = Field(ge=0)


class RetrievalSearchOutcome(BaseModel):
    """Budget-bounded hits for one tenant-scoped query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    hits: tuple[RetrievalHit, ...]


class RetrievalSearchResult(Params):
    value: dict[str, Any]


class RetrievalFreshnessParams(Params):
    """``retrieval.freshness`` takes no parameters; the tenant is the caller's."""


class RetrievalFreshnessOutcome(BaseModel):
    """Freshness of one tenant's retrieval sources."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    sources: dict[str, str]


class RetrievalFreshnessResult(Params):
    value: dict[str, Any]


@runtime_checkable
class Retriever(Protocol):
    """The one typed port GraphOS calls the engine retrieval through."""

    async def search(
        self, *, tenant: str, query: str, context_budget: int
    ) -> RetrievalSearchOutcome: ...

    async def freshness(self, *, tenant: str) -> RetrievalFreshnessOutcome: ...


async def handle_retrieval_search(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return budget-bounded hits for the caller's own tenant."""
    retriever: Retriever = bound_service(
        context, "retriever", reason="retriever is not composed"
    )
    context_budget = params["context_budget"]
    outcome = await retriever.search(
        tenant=context.caller.tenant,
        query=params["query"],
        context_budget=context_budget,
    )
    kept: list[RetrievalHit] = []
    used = 0
    for hit in outcome.hits:
        if used + hit.tokens > context_budget:
            break
        used += hit.tokens
        kept.append(hit)
    bounded = outcome.model_copy(update={"hits": tuple(kept)})
    return {"value": bounded.model_dump(mode="json")}


async def handle_retrieval_freshness(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Return source freshness for the caller's own tenant."""
    retriever: Retriever = bound_service(
        context, "retriever", reason="retriever is not composed"
    )
    outcome = await retriever.freshness(tenant=context.caller.tenant)
    return {"value": outcome.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    return (
        build_read_op(
            verb=Verb.FIND,
            op_id="retrieval.search",
            summary="Retrieve passages for a query within a context budget",
            examples=("find passages about this topic",),
            params=RetrievalSearchParams,
            result=RetrievalSearchResult,
            handler="graph_os.api.ops.retrieval.handle_retrieval_search",
            scope="memory:read",
        ),
        build_read_op(
            verb=Verb.FIND,
            op_id="retrieval.freshness",
            summary="Report freshness of the caller's retrieval sources",
            examples=("how fresh are my retrieval sources",),
            params=RetrievalFreshnessParams,
            result=RetrievalFreshnessResult,
            handler="graph_os.api.ops.retrieval.handle_retrieval_freshness",
            scope="memory:read",
        ),
    )


specs = operations

__all__ = [
    "RetrievalFreshnessOutcome",
    "RetrievalFreshnessParams",
    "RetrievalFreshnessResult",
    "RetrievalHit",
    "RetrievalSearchOutcome",
    "RetrievalSearchParams",
    "RetrievalSearchResult",
    "Retriever",
    "handle_retrieval_freshness",
    "handle_retrieval_search",
    "operations",
    "specs",
]
