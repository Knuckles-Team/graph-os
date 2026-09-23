"""EH-044: A2A inbound task routing asks EG's assembly first (Decide consumer).

When the caller declares the capabilities its task needs
(``graphOsCapabilityIris`` in message metadata) and graph-os booted with an
assembler (:mod:`graph_os.decide`), the task is routed by AU's
``route_a2a_task``: EG ``AgentAssemble`` proves the agent graph over the
operator-published agent-graph templates, and the solved record is committed
(``DecisionCommit``) before the route is used. On abstention, on EG being
unavailable, or when the record cannot be committed, the wrapped router's
current behaviour answers — a decision that could not be recorded is never
acted on. Budgeted requests keep their existing assembly path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .models import A2AMessage, A2ARouteDecision
from .routing import A2ARouter

logger = logging.getLogger(__name__)

__all__ = ["CAPABILITY_IRIS_METADATA_KEY", "DecideA2ARouter"]

#: Message metadata key carrying caller-declared native capability IRIs.
CAPABILITY_IRIS_METADATA_KEY = "graphOsCapabilityIris"
Templates = Callable[[], Sequence[Mapping[str, Any]]]


def _declared_capabilities(message: A2AMessage) -> tuple[str, ...]:
    declared = message.metadata.get(CAPABILITY_IRIS_METADATA_KEY) or []
    if not isinstance(declared, list) or not all(
        isinstance(iri, str) and iri.strip() for iri in declared
    ):
        raise ValueError(f"{CAPABILITY_IRIS_METADATA_KEY} must list capability IRIs")
    return tuple(sorted(set(declared)))


def _no_templates() -> Sequence[Mapping[str, Any]]:
    return ()


class DecideA2ARouter:
    """An :class:`A2ARouter` that consults EG assembly before ``inner``."""

    def __init__(
        self,
        inner: A2ARouter,
        assembler_for: Callable[[], Any],
        templates: Templates = _no_templates,
    ) -> None:
        self._inner = inner
        self._assembler_for = assembler_for
        self._templates = templates

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        decided = None
        if context_budget_tokens is None:
            decided = await self._decided(message)
        if decided is not None:
            return decided
        return await self._inner.route(
            message, context_budget_tokens=context_budget_tokens
        )

    async def _decided(self, message: A2AMessage) -> A2ARouteDecision | None:
        capabilities = _declared_capabilities(message)
        assembler = self._assembler_for()
        if not capabilities or assembler is None:
            return None
        from agent_utilities.decide.consumers.graphos import route_a2a_task

        try:
            answer = await route_a2a_task(
                assembler, capabilities, self._templates(), lambda reasons: reasons
            )
        except (RuntimeError, ConnectionError, TimeoutError, ValueError) as exc:
            # A commit refusal or transport failure costs only the fallback.
            logger.warning(
                "A2A decision not recorded (%s); current routing answers",
                type(exc).__name__,
            )
            return None
        agent, committed = answer.agent, answer.committed
        if agent is None or committed is None:
            logger.info("A2A decision fell back: %s", answer.reason)
            return None
        return A2ARouteDecision(
            agent_name=str(agent.get("agent_id")),
            selected_tools=tuple(
                str(tool.get("component_id")) for tool in agent.get("tools") or ()
            ),
            selection_mode="eg-decide-assembly",
            decision_record_ref=str(committed.get("record_id")),
        )
