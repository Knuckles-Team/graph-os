"""Governed agent routing for inbound A2A messages."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .models import A2AMessage, A2ARouteDecision

__all__ = ["A2AAssemblyUnavailable", "A2ARouter", "OrchestratorA2ARouter"]


class A2AAssemblyUnavailable(RuntimeError):
    """The required governed agent/tool assembly path is not executable."""


@runtime_checkable
class A2ARouter(Protocol):
    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision: ...


class OrchestratorA2ARouter:
    """Reuse AU capability resolution without inventing another router.

    The current EG ``AgentAssemble`` handler is an explicit refusal stub and the
    signed AU dispatch carrier cannot carry an allowed-tool subset. Requests
    asking for budgeted assembly therefore fail closed until those owning seams
    are live. Ordinary agent routing remains available through the canonical
    orchestrator capability resolver.
    """

    def __init__(self, engine_provider: Callable[[], Any]) -> None:
        self._engine_provider = engine_provider

    def _resolve(self, text: str) -> dict[str, Any]:
        from agent_utilities.orchestration.manager import Orchestrator

        engine = self._engine_provider()
        if engine is None:
            raise A2AAssemblyUnavailable("GraphOS routing authority is unavailable")
        result = Orchestrator(engine).resolve_capability(text)
        if not isinstance(result, dict):
            raise A2AAssemblyUnavailable(
                "GraphOS routing authority returned invalid data"
            )
        return result

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        if context_budget_tokens is not None:
            raise A2AAssemblyUnavailable(
                "budgeted AgentAssemble tool selection is not available"
            )
        result = await asyncio.to_thread(self._resolve, message.task_text())
        kind = str(result.get("kind") or "")
        name = str(result.get("name") or "").strip()
        if kind != "agent" or not name:
            raise A2AAssemblyUnavailable(
                "resolved capability requires unavailable governed assembly"
            )
        decision_ref = str(result.get("id") or "").strip() or None
        return A2ARouteDecision(
            agent_name=name,
            selection_mode="canonical-capability-router",
            decision_record_ref=decision_ref,
        )
