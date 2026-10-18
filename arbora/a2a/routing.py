"""Governed agent routing for inbound A2A messages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .models import A2AMessage, A2ARouteDecision

__all__ = [
    "A2AAssemblyUnavailable",
    "A2AOperationBridgeUnavailable",
    "A2ARouter",
    "OrchestratorA2ARouter",
]


class A2AAssemblyUnavailable(RuntimeError):
    """The required governed agent/tool assembly path is not executable."""


class A2AOperationBridgeUnavailable(RuntimeError):
    """``arbora.op/invoke``/``arbora.plan/confirm`` are not wired yet.

    ARBORA-A2A-R006 reuses the shared hosted-operation registry's
    ``invoke`` path (``Surface.A2A``) so a representative operation has
    identical authorization and error semantics through A2A, MCP and HTTP.
    That bridge does not exist yet, so both methods are registered on the
    A2A method table with validated params but fail closed rather than
    silently succeeding or being reported as unrecognized methods. This
    also keeps ARBORA-A2A-R005's human approval response
    (``arbora.plan/confirm``) disabled until the full signed exchange is
    implemented end to end.
    """


@runtime_checkable
class A2ARouter(Protocol):
    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision: ...


class OrchestratorA2ARouter:
    """Route an inbound message to the agent that its metadata names.

    Arbora does not rank task text against capabilities; that choice belongs
    to the engine. A message must name its agent in ``metadata.agent_name``.
    Budgeted assembly fails closed until the engine serves governed assembly.
    """

    def __init__(self, engine_provider: Callable[[], Any]) -> None:
        self._engine_provider = engine_provider

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        if context_budget_tokens is not None:
            raise A2AAssemblyUnavailable(
                "budgeted AgentAssemble tool selection is not available"
            )
        name = str(message.metadata.get("agent_name") or "").strip()
        if not name:
            raise A2AAssemblyUnavailable(
                "the message must name its agent in metadata.agent_name"
            )
        return A2ARouteDecision(
            agent_name=name,
            selection_mode="named-agent",
        )
