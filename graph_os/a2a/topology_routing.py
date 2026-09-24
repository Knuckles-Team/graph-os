"""ST-9: A2A swarm topology through EG (SWARM-TOPOLOGY-DECIDE-DESIGN §1, §7).

A task that declares swarm task-shape classes (``graphOsTaskShapeIris``) is
asked the topology question -- EG ``AgentAssemble`` with
``requirements.topology`` over the published topology templates -- and only a
committed, certified plan runs:

1. **Decide + commit.** AU's :func:`ask_topology` over graph-os's assembler,
   whose commit context graph-os mints (AU never does).
2. **Acquire.** Every ``LeasePlan`` entry in ONE ``AcquireCapacity`` (the
   ledger admits all demands or none), bound to the plan's decision identity
   and idempotent on it. The plan never grants capacity (invariant T1).
3. **Re-decide once.** On a capacity denial the question is asked again with
   a fresh headroom observation; a second denial or an abstention abstains
   UPWARD (:class:`A2AAssemblyUnavailable`). There is no heuristic fallback,
   no delegation and no lease left behind.
4. **Publish + route.** The routed graph is published with the committed
   record as ``synthesis_evidence``; the route carries the record and the
   digest of the runtime admission built from the plan
   (``ElasticTopologyAdmission``): the run can be no wider, deeper or longer.

Leases are TTL-bounded and released explicitly when dispatch fails or the
task is cancelled (:meth:`TopologyA2ARouter.release`); the continuation point
(``au.swarm.continue``) releases amount early when a run narrows.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .decide_routing import publish_routed
from .models import A2AMessage, A2ARouteDecision
from .routing import A2AAssemblyUnavailable, A2ARouter, typed_task_iri

logger = logging.getLogger(__name__)

__all__ = [
    "SUBTASKS_METADATA_KEY",
    "TASK_SHAPE_METADATA_KEY",
    "PlanLeaseBook",
    "PlanLeases",
    "TopologyA2ARouter",
    "TopologyScope",
]

#: Message metadata key carrying the caller's swarm task-shape class IRIs.
TASK_SHAPE_METADATA_KEY = "graphOsTaskShapeIris"
#: Message metadata key carrying the task's independent subtask count.
SUBTASKS_METADATA_KEY = "graphOsSubtasks"
_ACQUIRED = frozenset({"accepted", "replayed"})
_RECOVERABLE = (RuntimeError, ConnectionError, TimeoutError, ValueError)


def _payload(value: Any) -> Any:
    return getattr(value, "payload", value)


def declared_task_shapes(message: A2AMessage) -> tuple[str, ...]:
    declared = message.metadata.get(TASK_SHAPE_METADATA_KEY) or []
    if not isinstance(declared, list) or not all(
        isinstance(iri, str) and iri.strip() for iri in declared
    ):
        raise ValueError(f"{TASK_SHAPE_METADATA_KEY} must list class IRIs")
    return tuple(sorted(set(declared)))


def _subtasks(message: A2AMessage) -> int:
    raw = message.metadata.get(SUBTASKS_METADATA_KEY, 1)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 4096:
        raise ValueError(f"{SUBTASKS_METADATA_KEY} must be an integer in 1..4096")
    return raw


@dataclass(frozen=True)
class TopologyScope:
    """The capacity a topology plan must fit, set by graph-os's deployment."""

    cells: tuple[str, ...]
    priority: str = "orchestration"
    ttl_ms: int = 15 * 60 * 1000


@dataclass(frozen=True)
class PlanLeases:
    """The leases one committed plan holds."""

    record_id: str
    fences: tuple[Mapping[str, Any], ...]


class PlanLeaseBook:
    """``AcquireCapacity``/``ReleaseCapacity`` for committed plans (graph-os only)."""

    def __init__(
        self,
        client: Any,
        *,
        tenant_ref: str,
        owner_digest: str,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._capacity = client.capacity_leases
        self._tenant = tenant_ref
        self._owner = owner_digest
        self._clock_ms = clock_ms

    async def acquire(
        self, plan: Mapping[str, Any], record_id: str, ttl_ms: int
    ) -> PlanLeases | None:
        """Every lease of ``plan`` at once, or ``None`` when the ledger denies."""
        lease = plan.get("lease") or {}
        demands = [
            {
                "cell_id": str(entry["cell_id"]),
                "resource_class": str(entry["class"]),
                "amount": int(entry["amount"]),
            }
            for entry in lease.get("per_cell") or ()
        ]
        if not demands:
            return PlanLeases(record_id, ())
        attempt = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
        answer = _payload(
            await self._capacity.acquire(
                {
                    "schema_version": "1",
                    "tenant_ref": self._tenant,
                    "work_item_id": f"topology:{attempt[:48]}",
                    "owner_digest": self._owner,
                    "idempotency_key": f"topology-plan:{attempt}",
                    "priority": str(lease.get("priority") or "orchestration"),
                    "demands": demands,
                    "lease_id": None,
                    "ttl_ms": ttl_ms,
                    "now_ms": self._clock_ms(),
                    "cost_budget_micros": None,
                    "token_budget": None,
                }
            )
        )
        if str(answer["decision"]) not in _ACQUIRED or not answer["leases"]:
            return None
        fences = tuple(
            {
                "lease_id": str(row["lease_id"]),
                "lease_epoch": int(row["lease_epoch"]),
                "fence_token": int(row["fence_token"]),
            }
            for row in answer["leases"]
        )
        return PlanLeases(record_id, fences)

    async def release(self, leases: PlanLeases) -> None:
        """Return every lease of a plan (idempotent per fence)."""
        if not leases.fences:
            return
        await self._capacity.release(
            {
                "schema_version": "1",
                "tenant_ref": self._tenant,
                "owner_digest": self._owner,
                "leases": [dict(fence) for fence in leases.fences],
                "now_ms": self._clock_ms(),
                "ttl_ms": None,
                "idempotency_key": f"{leases.record_id}:release",
            }
        )


def _record_id(answer: Any) -> str:
    record = (answer.result or {}).get("record") or {}
    return str(record.get("record_id") or "")


@dataclass
class TopologyA2ARouter:
    """Routes swarm-topology tasks through EG; every other task to ``inner``."""

    inner: A2ARouter
    decide_for: Callable[[], Any]
    templates: Callable[[], Sequence[Mapping[str, Any]]]
    lease_book_for: Callable[[Any], PlanLeaseBook]
    scope_for: Callable[[], TopologyScope]
    _held: dict[str, PlanLeases] = field(default_factory=dict)

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        shapes = declared_task_shapes(message)
        if not shapes:
            return await self.inner.route(
                message, context_budget_tokens=context_budget_tokens
            )
        composition = self.decide_for()
        if composition is None:
            raise A2AAssemblyUnavailable("topology routing needs EG Decide")
        return await self._planned(composition, message, shapes)

    async def _ask(
        self, composition: Any, message: A2AMessage, shapes: tuple[str, ...]
    ) -> Any:
        from agent_utilities.decide.consumers.topology import TopologyAsk, ask_topology

        scope = self.scope_for()
        ask = TopologyAsk(
            task_classes=shapes,
            subtasks=_subtasks(message),
            cells=scope.cells,
            priority=scope.priority,
        )
        answer = await ask_topology(composition.assembler, ask, self.templates())
        if answer.committed is None:
            raise A2AAssemblyUnavailable(f"topology abstained upward: {answer.reason}")
        return answer

    async def _planned(
        self, composition: Any, message: A2AMessage, shapes: tuple[str, ...]
    ) -> A2ARouteDecision:
        from agent_utilities.decide.consumers.topology import plan_of

        book = self.lease_book_for(composition)
        for _attempt in range(2):
            answer = await self._ask(composition, message, shapes)
            record_id = _record_id(answer)
            plan = plan_of(answer.result) or {}
            leases = await book.acquire(plan, record_id, self.scope_for().ttl_ms)
            if leases is not None:
                return await self._admitted(composition, message, answer, plan, leases)
            logger.info("capacity denied plan %s; re-deciding once", record_id)
        raise A2AAssemblyUnavailable("capacity denied the plan twice; abstained upward")

    async def _admitted(
        self,
        composition: Any,
        message: A2AMessage,
        answer: Any,
        plan: Mapping[str, Any],
        leases: PlanLeases,
    ) -> A2ARouteDecision:
        from agent_utilities.graph.plan_admission import admission_from_plan

        record_id = leases.record_id
        try:
            admission = admission_from_plan(
                plan,
                record_id=record_id,
                tenant=str(composition.assembler.tenant),
                delegation_id=f"a2a:{message.message_id}",
            )
        except ValueError:
            await self.lease_book_for(composition).release(leases)
            raise
        self._held[record_id] = leases
        agent = answer.agent or {}
        return A2ARouteDecision(
            agent_name=str(agent.get("agent_id") or record_id),
            selection_mode="eg-decide-topology",
            agent_graph_ref=await publish_routed(composition, answer),
            run_spec_ref=f"admission:{admission.digest}",
            decision_record_ref=record_id,
            task_iri=typed_task_iri(message),
        )

    async def release(self, decision: A2ARouteDecision) -> bool:
        """Release a routed plan's leases (dispatch failed, or the task stopped)."""
        record_id = decision.decision_record_ref or ""
        leases = self._held.pop(record_id, None)
        composition = self.decide_for()
        if leases is None or composition is None:
            return False
        try:
            await self.lease_book_for(composition).release(leases)
        except _RECOVERABLE as exc:
            logger.warning(
                "plan %s leases left to expire (%s)", record_id, type(exc).__name__
            )
            return False
        return True
