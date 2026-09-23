"""EH-044: A2A inbound task routing through EG assembly (Decide consumer).

Every typed A2A task — native ``eg:task/*`` IRIs (``graphOsTaskIris``) and/or
capability IRIs (``graphOsCapabilityIris``) — is first routed by AU's
``route_a2a_task`` over the boot-installed assembler (:mod:`graph_os.decide`):
EG ``AgentAssemble`` proves the agent graph, the solved record is committed
(``DecisionCommit``), the assembled agents and then the routed graph are
published with the committed record as ``synthesis_evidence``, and only then
is the route used. Free text never becomes a requirement: it travels only as
its digest, which EG answers with an ``unmapped_task`` abstention.

* Unbudgeted: abstention, EG unavailability, a refused commit, or no installed
  Decide falls back to the current routing (AU control-plane capability
  search). A decision that could not be recorded is never acted on.
* Budgeted (``context_budget_tokens``): there is no weaker route that honours a
  budget, so every non-solved outcome fails closed with
  :class:`A2AAssemblyUnavailable` naming why — a budget is never ignored.

A failed publish after a successful commit is logged and the route is still
used: the decision itself is durable; the graph publish is its projection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .models import A2AMessage, A2ARouteDecision
from .routing import (
    A2AAssemblyUnavailable,
    A2ARouter,
    declared_task_iris,
    typed_task_iri,
)

logger = logging.getLogger(__name__)

__all__ = ["CAPABILITY_IRIS_METADATA_KEY", "DecideA2ARouter"]

#: Message metadata key carrying caller-declared native capability IRIs.
CAPABILITY_IRIS_METADATA_KEY = "graphOsCapabilityIris"
Templates = Callable[[], Sequence[Mapping[str, Any]]]
_RECOVERABLE = (RuntimeError, ConnectionError, TimeoutError, ValueError)


def _declared_capabilities(message: A2AMessage) -> tuple[str, ...]:
    declared = message.metadata.get(CAPABILITY_IRIS_METADATA_KEY) or []
    if not isinstance(declared, list) or not all(
        isinstance(iri, str) and iri.strip() for iri in declared
    ):
        raise ValueError(f"{CAPABILITY_IRIS_METADATA_KEY} must list capability IRIs")
    return tuple(sorted(set(declared)))


def _no_templates() -> Sequence[Mapping[str, Any]]:
    return ()


def _payload(value: Any) -> Any:
    return getattr(value, "payload", value)


async def _publish_agents(composition: Any, answer: Any) -> None:
    """Publish each assembled agent; the routed graph pins them."""
    from epistemic_graph.generated.storage import send_agent_library

    graphs = composition.assembler.graphs
    for entry in (answer.result or {}).get("agents") or ():
        agent_id = str(entry.get("agent_id"))
        context = composition.authority.context(
            kind="agent_library_publish",
            target=agent_id,
            purpose_id="decision:routed-agent",
            idempotency_key=f"routed-agent:{agent_id}:{entry.get('version', '')}",
        )
        await send_agent_library(
            graphs.client,
            {
                "op": {
                    "operation": "publish",
                    "request": {
                        "context": context.model_dump(mode="json"),
                        "entry": dict(entry),
                    },
                }
            },
            graphs.graph,
        )


async def _publish_routed(composition: Any, answer: Any) -> str | None:
    """Publish agents then the graph (evidence = the committed record)."""
    graph = (answer.result or {}).get("graph") or {}
    graph_id = str(graph.get("graph_id") or "")
    try:
        await _publish_agents(composition, answer)
        context = composition.authority.context(
            kind="agent_graph_publish",
            target=graph_id,
            purpose_id="decision:routed-graph",
            idempotency_key=f"routed-graph:{graph_id}:{graph.get('version', '')}",
        )
        await composition.assembler.publish_routed(
            answer,
            context.model_dump(mode="json"),
            idempotency_key=f"routed-graph:{_payload(answer.committed)['record_id']}",
        )
    except _RECOVERABLE as exc:
        logger.warning(
            "routed graph %s not published (%s); the committed decision stands",
            graph_id,
            type(exc).__name__,
        )
        return None
    return graph_id or None


class DecideA2ARouter:
    """An :class:`A2ARouter` that routes typed tasks through EG assembly first."""

    def __init__(
        self,
        inner: A2ARouter,
        decide_for: Callable[[], Any],
        templates: Templates = _no_templates,
    ) -> None:
        self._inner = inner
        self._decide_for = decide_for
        self._templates = templates

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        if context_budget_tokens is not None:
            return await self._budgeted(message, context_budget_tokens)
        decided, _reason = await self._decided(message, None)
        if decided is not None:
            return decided
        return await self._inner.route(message, context_budget_tokens=None)

    async def _budgeted(self, message: A2AMessage, budget: int) -> A2ARouteDecision:
        decided, reason = await self._decided(message, budget)
        if decided is None:
            raise A2AAssemblyUnavailable(f"budgeted routing failed closed: {reason}")
        return decided

    async def _decided(
        self, message: A2AMessage, budget: int | None
    ) -> tuple[A2ARouteDecision | None, str]:
        composition = self._decide_for()
        if composition is None:
            return None, "no_runner"
        from agent_utilities.decide.consumers.graphos import route_a2a_task

        try:
            answer = await route_a2a_task(
                composition.assembler,
                lambda reasons: reasons,
                task_iris=declared_task_iris(message),
                capabilities=_declared_capabilities(message),
                context_budget_tokens=budget,
                templates=self._templates(),
                text=message.task_text(),
            )
        except _RECOVERABLE as exc:
            logger.warning("A2A decision not recorded (%s)", type(exc).__name__)
            return None, f"not_recorded: {type(exc).__name__}"
        if answer.agent is None or answer.committed is None:
            return None, answer.reason
        return await self._route(composition, message, answer), answer.reason

    @staticmethod
    async def _route(
        composition: Any, message: A2AMessage, answer: Any
    ) -> A2ARouteDecision:
        agent = answer.agent
        return A2ARouteDecision(
            agent_name=str(agent.get("agent_id")),
            selected_tools=tuple(
                str(tool.get("component_id")) for tool in agent.get("tools") or ()
            ),
            selection_mode="eg-decide-assembly",
            agent_graph_ref=await _publish_routed(composition, answer),
            decision_record_ref=str(_payload(answer.committed)["record_id"]),
            task_iri=typed_task_iri(message),
        )
