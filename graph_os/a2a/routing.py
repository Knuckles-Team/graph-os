"""Governed agent routing for inbound A2A messages.

Ordinary routing asks AU's public control plane to resolve the message to an
authorized agent capability (EG ``AgentComponent`` search). A request that
carries a context budget is routed by EG ``AgentAssemble`` instead, which
proves the smallest agent graph and tool subset covering the task; that path
fails closed until the connected engine serves the method.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent_utilities.api import CapabilitySearchRequest

from .models import A2AMessage, A2ARouteDecision

__all__ = [
    "A2AAssemblyUnavailable",
    "A2ARouter",
    "AssemblyRouter",
    "ControlPlaneA2ARouter",
    "EgAssemblyRouter",
    "TASK_IRIS_METADATA_KEY",
]


class A2AAssemblyUnavailable(RuntimeError):
    """The required governed agent/tool assembly path is not executable."""


@runtime_checkable
class A2ARouter(Protocol):
    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision: ...


@runtime_checkable
class AssemblyRouter(Protocol):
    """Budgeted assembly: the proved agent and its covering tool subset."""

    async def assemble(
        self, message: A2AMessage, *, context_budget_tokens: int
    ) -> A2ARouteDecision: ...


class ControlPlaneA2ARouter:
    """Route through the caller's AU control plane or the EG assembly port."""

    def __init__(self, control_plane_for: Any, assembly: AssemblyRouter) -> None:
        self._control_plane_for = control_plane_for
        self._assembly = assembly

    async def route(
        self, message: A2AMessage, *, context_budget_tokens: int | None
    ) -> A2ARouteDecision:
        if context_budget_tokens is not None:
            return await self._assembly.assemble(
                message, context_budget_tokens=context_budget_tokens
            )
        from agent_utilities.api import resolve_session

        session = resolve_session(required_scope="kg:read")
        resolution = await self._control_plane_for(session).resolve_capability(
            CapabilitySearchRequest(task=message.task_text())
        )
        if resolution.kind != "agent":
            raise A2AAssemblyUnavailable(
                "resolved capability requires unavailable governed assembly"
            )
        return A2ARouteDecision(
            agent_name=resolution.name,
            selection_mode="canonical-capability-router",
            decision_record_ref=resolution.component_id or None,
        )


#: Message metadata key carrying caller-declared native ``eg:task/*`` IRIs.
TASK_IRIS_METADATA_KEY = "graphOsTaskIris"
_ASSEMBLY_KINDS = ("a2a_agent_card", "tool")


def _declared_task_iris(message: A2AMessage) -> tuple[str, ...]:
    declared = message.metadata.get(TASK_IRIS_METADATA_KEY) or []
    if not isinstance(declared, list) or not all(
        isinstance(iri, str) and iri.startswith("eg:task/") for iri in declared
    ):
        raise ValueError(f"{TASK_IRIS_METADATA_KEY} must list eg:task/ IRIs")
    return tuple(declared)


class EgAssemblyRouter:
    """Budgeted routing through EG ``AgentAssemble`` (fail closed until served)."""

    def __init__(self, assembly: Any) -> None:
        self._assembly = assembly

    async def assemble(
        self, message: A2AMessage, *, context_budget_tokens: int
    ) -> A2ARouteDecision:
        from agent_utilities.api import resolve_session

        from graph_os.assembly import (
            AssemblyAbstained,
            AssemblyRequirements,
            AssemblyUnavailable,
        )

        session = resolve_session(required_scope="kg:read")
        requirements = AssemblyRequirements(
            text=message.task_text(),
            context_budget_tokens=context_budget_tokens,
            kinds=_ASSEMBLY_KINDS,
            task_iris=_declared_task_iris(message),
        )
        try:
            outcome = await self._assembly.assemble(session, requirements)
        except (AssemblyUnavailable, AssemblyAbstained) as exc:
            raise A2AAssemblyUnavailable(str(exc)) from exc
        if not outcome.agent_id:
            raise A2AAssemblyUnavailable("AgentAssemble proved no agent")
        return A2ARouteDecision(
            agent_name=outcome.agent_id,
            selected_tools=outcome.tool_ids,
            selection_mode="eg-agent-assemble",
            decision_record_ref=outcome.record_id or None,
        )
