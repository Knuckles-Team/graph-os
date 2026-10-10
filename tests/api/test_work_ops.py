"""Focused contract for the GRAPHOS-OPS-R022.1 hosted work operations.

Exercises the handler directly via ``tests/api/_ops_support``'s shared
context builder; full-registry wiring is covered separately by
``test_registry_factory.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops import work
from graph_os.api.registry import Effect, Registry, Surface
from tests.api._ops_support import service_context


class _FakeRunner:
    def __init__(self) -> None:
        self.items = {"item-1": {"item_id": "item-1", "title": "triage a ticket"}}
        self.offers: list[dict[str, Any]] = []

    async def list_items(
        self, *, tenant: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        return {"tenant": tenant, "items": list(self.items.values())[:limit]}

    async def get_item(self, item_id: str, *, tenant: str) -> dict[str, Any] | None:
        item = self.items.get(item_id)
        if item is None:
            return None
        return {**item, "tenant": tenant}

    async def create_offer(
        self, item_id: str, *, terms: dict[str, Any], tenant: str, idempotency_key: str
    ) -> dict[str, Any]:
        offer = {
            "item_id": item_id,
            "terms": terms,
            "tenant": tenant,
            "idempotency_key": idempotency_key,
        }
        self.offers.append(offer)
        return offer


def _context(**services: object):
    return service_context(tenant="tenant:one", **services)


def test_work_ops_declare_exact_scopes_and_effects() -> None:
    registry = Registry(work.operations())
    assert len(registry) == 4
    list_op = registry["work.items.list"]
    get_op = registry["work.items.get"]
    offer_op = registry["work.offers.create"]
    assert list_op.scopes == frozenset({"work:read"})
    assert get_op.scopes == frozenset({"work:read"})
    assert offer_op.scopes == frozenset({"work:write"})
    assert list_op.effect is Effect.READ
    assert offer_op.effect is Effect.WRITE
    assert Surface.MCP in list_op.surfaces
    assert all(op.examples for op in registry)


@pytest.mark.asyncio
async def test_items_list_handler_reports_unbound_runner_closed() -> None:
    op = work.operations()[0]
    with pytest.raises(RuntimeError, match="not bound"):
        await work.handle_work(_context(), {"cursor": None, "limit": 50}, op)


@pytest.mark.asyncio
async def test_items_list_handler_scopes_to_caller_tenant() -> None:
    runner = _FakeRunner()
    op = work.operations()[0]
    result = await work.handle_work(
        _context(work_runner=runner), {"cursor": None, "limit": 50}, op
    )
    assert result == {
        "value": {"tenant": "tenant:one", "items": list(runner.items.values())}
    }


@pytest.mark.asyncio
async def test_items_get_handler_reports_missing_item() -> None:
    runner = _FakeRunner()
    op = work.operations()[1]
    with pytest.raises(LookupError, match="not found"):
        await work.handle_work(_context(work_runner=runner), {"item_id": "absent"}, op)


@pytest.mark.asyncio
async def test_items_get_handler_reads_back_bound_item() -> None:
    runner = _FakeRunner()
    op = work.operations()[1]
    result = await work.handle_work(
        _context(work_runner=runner), {"item_id": "item-1"}, op
    )
    assert result == {
        "value": {
            "item_id": "item-1",
            "title": "triage a ticket",
            "tenant": "tenant:one",
        }
    }


@pytest.mark.asyncio
async def test_offers_create_handler_requires_idempotency_key() -> None:
    runner = _FakeRunner()
    op = work.operations()[2]
    context = _context(work_runner=runner)
    context.idempotency_key = None
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await work.handle_work(context, {"item_id": "item-1", "terms": {}}, op)


@pytest.mark.asyncio
async def test_offers_create_handler_records_a_bound_offer() -> None:
    runner = _FakeRunner()
    op = work.operations()[2]
    result = await work.handle_work(
        _context(work_runner=runner),
        {"item_id": "item-1", "terms": {"rate": "standard"}},
        op,
    )
    assert result == {
        "value": {
            "item_id": "item-1",
            "terms": {"rate": "standard"},
            "tenant": "tenant:one",
            "idempotency_key": "key-1",
        }
    }
    assert runner.offers == [result["value"]]


class _FakeEvolutionRunner:
    async def get_loop_status(self, *, tenant: str, loop_id: str) -> dict[str, Any]:
        return {"tenant": tenant, "loop_id": loop_id, "state": "running"}


@pytest.mark.spec("GRAPHOS-OPS-R022.2.1")
@pytest.mark.asyncio
async def test_evolution_loop_status_reads_runner_with_caller_tenant() -> None:
    registry = Registry(work.operations())
    op = registry["evolution.loops.status"]
    assert op.effect is Effect.READ
    assert op.scopes == frozenset({"work:read"})
    result = await work.handle_evolution_loop_status(
        _context(evolution_runner=_FakeEvolutionRunner()), {"loop_id": "loop-1"}, op
    )
    assert result == {
        "value": {"tenant": "tenant:one", "loop_id": "loop-1", "state": "running"}
    }


@pytest.mark.spec("GRAPHOS-OPS-R022.2.1")
@pytest.mark.asyncio
async def test_evolution_loop_status_fails_closed_without_runner() -> None:
    op = Registry(work.operations())["evolution.loops.status"]
    with pytest.raises(OperationRefused) as refused:
        await work.handle_evolution_loop_status(_context(), {"loop_id": "loop-1"}, op)
    assert refused.value.code == "UNAVAILABLE"
