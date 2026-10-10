"""Focused authority tests for ``retrieval.search`` (GRAPHOS-OPS-R021.2.1)."""

from __future__ import annotations

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import retrieval
from graph_os.api.ops.retrieval import RetrievalHit, RetrievalSearchOutcome
from tests.api._ops_support import tenant_store_context


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search(
        self, *, tenant: str, query: str, context_budget: int
    ) -> RetrievalSearchOutcome:
        self.calls.append((tenant, query, context_budget))
        return RetrievalSearchOutcome(
            tenant=tenant,
            hits=(
                RetrievalHit(ref="a", text="one", tokens=60),
                RetrievalHit(ref="b", text="two", tokens=60),
            ),
        )


def _context(retriever: object | None):
    return tenant_store_context(store=retriever, service_name="retriever")


@pytest.mark.spec("GRAPHOS-OPS-R021.2.1")
async def test_search_reaches_engine_under_callers_tenant_within_budget(
    op_by_id,
) -> None:
    fake = _FakeRetriever()
    op = op_by_id(retrieval.operations(), "retrieval.search")
    result = await retrieval.handle_retrieval_search(
        _context(fake), {"query": "q", "context_budget": 100}, op
    )
    assert fake.calls == [("tenant-a", "q", 100)]
    assert result["value"]["tenant"] == "tenant-a"
    assert [h["ref"] for h in result["value"]["hits"]] == ["a"]
    assert op.scopes == frozenset({"memory:read"})


@pytest.mark.spec("GRAPHOS-OPS-R021.2.1")
async def test_search_fails_closed_when_retriever_not_composed(op_by_id) -> None:
    op = op_by_id(retrieval.operations(), "retrieval.search")
    with pytest.raises(OperationRefused) as excinfo:
        await retrieval.handle_retrieval_search(
            _context(None), {"query": "q", "context_budget": 100}, op
        )
    assert excinfo.value.code == "UNAVAILABLE"
