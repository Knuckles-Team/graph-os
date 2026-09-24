"""ST-9: A2A swarm topology -- decide, commit, acquire all-or-nothing, re-decide
once on denial, publish, route; never a heuristic, never a leaked lease."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.a2a.models import A2AMessage, A2ARouteDecision, A2ATask, A2ATextPart
from graph_os.a2a.routing import A2AAssemblyUnavailable
from graph_os.a2a.service import A2AService
from graph_os.a2a.topology_routing import (
    SUBTASKS_METADATA_KEY,
    TASK_SHAPE_METADATA_KEY,
    PlanLeaseBook,
    TopologyA2ARouter,
    TopologyScope,
)
from graph_os.decide_topology import TopologyCatalog, _scope_of

from .fakes import composition, record

SHAPE = "http://knuckles.team/kg/swarm#IndependentSubtasks"
PLAN = {
    "class_iri": "http://knuckles.team/kg/swarm#FanOutJoin",
    "slots": [
        {"node_id": "lead", "width": 1, "rounds": 1},
        {"node_id": "worker", "width": 3, "rounds": 1},
    ],
    "stop": {"rule": "max_rounds", "n": 1},
    "lease": {
        "per_cell": [{"cell_id": "cell-llm", "class": "llm_generator", "amount": 4}],
        "priority": "orchestration",
    },
    "allowances": [],
}
CURRENT = A2ARouteDecision(agent_name="current-agent", selection_mode="current")


def _planned() -> dict[str, Any]:
    solved = record("solved")
    solved["outcome"]["topology"] = PLAN
    return {
        "record": solved,
        "graph": {"graph_id": "graph-fan", "version": "1"},
        "agents": [{"agent_id": "agent-lead", "version": "1", "tools": []}],
    }


class _Capacity:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = list(decisions)
        self.acquired: list[dict[str, Any]] = []
        self.released: list[dict[str, Any]] = []

    async def acquire(self, request: dict[str, Any]) -> dict[str, Any]:
        self.acquired.append(request)
        decision = self.decisions.pop(0)
        leases = (
            []
            if decision == "exhausted"
            else [{"lease_id": "lease-1", "lease_epoch": 2, "fence_token": 9}]
        )
        return {"decision": decision, "leases": leases}

    async def release(self, request: dict[str, Any]) -> dict[str, Any]:
        self.released.append(request)
        return {"decision": "released"}


class _Inner:
    def __init__(self) -> None:
        self.calls = 0

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        self.calls += 1
        return CURRENT


def _message(shapes: list[str] | None = None) -> A2AMessage:
    metadata: dict[str, Any] = {}
    if shapes is not None:
        metadata[TASK_SHAPE_METADATA_KEY] = shapes
        metadata[SUBTASKS_METADATA_KEY] = 3
    return A2AMessage(
        role="user",
        message_id="m-1",
        parts=[A2ATextPart(text="survey the three vendors")],
        metadata=metadata,
    )


@pytest.fixture
def no_agent_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    async def send_agent_library(client: Any, params: Any, graph: Any) -> Any:
        return {"ok": True}

    monkeypatch.setattr(
        "epistemic_graph.generated.storage.send_agent_library", send_agent_library
    )


def _router(
    answer: dict[str, Any], capacity: _Capacity
) -> tuple[TopologyA2ARouter, Any, _Inner]:
    wired = composition(answer)
    inner = _Inner()
    book = PlanLeaseBook(
        SimpleNamespace(capacity_leases=capacity),
        tenant_ref="tenant-a",
        owner_digest="principal:sha256:" + "a" * 64,
        clock_ms=lambda: 1_000,
    )
    router = TopologyA2ARouter(
        inner=inner,
        decide_for=lambda: wired,
        templates=lambda: [{"graph_id": "swarm:fan-out-join"}],
        lease_book_for=lambda _composition: book,
        scope_for=lambda: TopologyScope(cells=("cell-llm",)),
    )
    return router, wired, inner


def test_a_plan_is_committed_leased_published_and_routed(
    no_agent_publish: None,
) -> None:
    capacity = _Capacity(["accepted"])
    router, wired, _inner = _router(_planned(), capacity)
    decision = asyncio.run(router.route(_message([SHAPE]), context_budget_tokens=None))
    assert decision.selection_mode == "eg-decide-topology"
    assert decision.decision_record_ref == record("solved")["record_id"]
    assert (decision.run_spec_ref or "").startswith("admission:")
    asked = wired.assembler.graphs.requests[0]["requirements"]["topology"]
    assert asked["task_classes"] == [SHAPE] and asked["subtasks"] == 3
    assert asked["capacity"]["cells"] == ["cell-llm"]
    assert wired.assembler.graphs.commits, "only a committed plan runs"
    [acquired] = capacity.acquired
    assert acquired["demands"] == [
        {"cell_id": "cell-llm", "resource_class": "llm_generator", "amount": 4}
    ]
    assert acquired["idempotency_key"].startswith("topology-plan:")
    assert wired.assembler.graphs.published, "the routed graph is published"


def test_one_denial_re_decides_once_with_fresh_headroom(no_agent_publish: None) -> None:
    capacity = _Capacity(["exhausted", "accepted"])
    router, wired, _inner = _router(_planned(), capacity)
    asyncio.run(router.route(_message([SHAPE]), context_budget_tokens=None))
    assert len(wired.assembler.graphs.requests) == 2
    assert len(capacity.acquired) == 2


def test_two_denials_abstain_upward_with_no_delegation_and_no_lease() -> None:
    capacity = _Capacity(["exhausted", "exhausted"])
    router, wired, inner = _router(_planned(), capacity)
    with pytest.raises(A2AAssemblyUnavailable, match="denied the plan twice"):
        asyncio.run(router.route(_message([SHAPE]), context_budget_tokens=None))
    assert inner.calls == 0, "never a heuristic fallback"
    assert wired.assembler.graphs.published == []
    assert router._held == {}


def test_an_abstention_is_raised_upward_before_any_lease() -> None:
    capacity = _Capacity([])
    router, _wired, inner = _router(
        {"record": record("abstained"), "agents": []}, capacity
    )
    with pytest.raises(A2AAssemblyUnavailable, match="abstained upward"):
        asyncio.run(router.route(_message([SHAPE]), context_budget_tokens=None))
    assert capacity.acquired == [] and inner.calls == 0


def test_a_task_without_task_shapes_takes_the_ordinary_route() -> None:
    router, _wired, inner = _router(_planned(), _Capacity([]))
    assert asyncio.run(router.route(_message(), context_budget_tokens=None)) is CURRENT
    assert inner.calls == 1


class _FailingAuthority:
    """An :class:`A2ATaskAuthority` whose dispatch is refused."""

    async def dispatch(
        self, *, message: A2AMessage, idempotency_key: str, decision: A2ARouteDecision
    ) -> A2ATask:
        raise RuntimeError("dispatch refused")

    async def get(self, task_id: str) -> A2ATask | None:
        return None

    async def list(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[list[A2ATask], str | None]:
        return [], None

    async def cancel(self, task_id: str) -> A2ATask:
        raise LookupError(task_id)

    async def output(self, task_id: str) -> str | None:
        return None


def test_a_failed_dispatch_releases_the_plan_s_leases(no_agent_publish: None) -> None:
    capacity = _Capacity(["accepted"])
    router, _wired, _inner = _router(_planned(), capacity)
    service = A2AService(authority=_FailingAuthority(), router=router)
    with pytest.raises(RuntimeError, match="dispatch refused"):
        asyncio.run(
            service.send_message(message=_message([SHAPE]), idempotency_key="k-1")
        )
    [released] = capacity.released
    assert released["leases"] == [
        {"lease_id": "lease-1", "lease_epoch": 2, "fence_token": 9}
    ]
    assert router._held == {}


def test_the_scope_is_one_tenant_cell_per_resource_class() -> None:
    scope = _scope_of(
        [
            {"cell_id": "gpu-b", "resource_class": "gpu"},
            {"cell_id": "gpu-a", "resource_class": "gpu"},
            {"cell_id": "llm", "resource_class": "llm_generator"},
        ]
    )
    assert scope.cells == ("gpu-a", "llm")


def test_a_failed_catalog_refresh_keeps_the_last_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unreachable(client: Any, params: Any, graph: Any) -> Any:
        raise ConnectionError("engine unreachable")

    monkeypatch.setattr(
        "epistemic_graph.generated.storage.send_agent_graph", unreachable
    )
    catalog = TopologyCatalog(graph_ids=("swarm:single",))
    graphs = SimpleNamespace(client=SimpleNamespace(), graph="tenant-a")
    assert asyncio.run(catalog.refresh(graphs, "tenant-a")) is False
    assert catalog.templates() == () and catalog.scope.cells == ()


def test_a_stopped_run_returns_its_plan_s_leases_through_the_router(
    no_agent_publish: None,
) -> None:
    capacity = _Capacity(["accepted"])
    router, _wired, _inner = _router(_planned(), capacity)
    decision = asyncio.run(router.route(_message([SHAPE]), context_budget_tokens=None))
    record_id = decision.decision_record_ref or ""
    assert asyncio.run(router.release_record(record_id)) is True
    assert asyncio.run(router.release_record(record_id)) is False, "idempotent"
    assert len(capacity.released) == 1
