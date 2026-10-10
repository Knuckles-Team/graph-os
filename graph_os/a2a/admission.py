"""Typed context-budget tool-subset admission for the A2A door.

Owned by ``GRAPHOS-A2A-002``: admission, refusal, and provenance for the
smallest authorized tool subset that covers a selected agent graph within a
caller's context budget (see ``docs/a2a.md``, "Tool-subset assembly
status"). This fails closed today because epistemic-graph's
``AgentAssemble`` operation returns unavailable and the signed AU
``AgentTurnEnvelope`` carries no allowed-tool-subset field
(agent-utilities ``AU-CONTROL-R035``). Neither upstream gap is bridged with a
local heuristic, a cached prior subset, or an advisory best-effort answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import A2AContextBudget, _WireModel

__all__ = [
    "ToolSubsetAdmissionDecision",
    "ToolSubsetAdmissionRefusalReason",
    "ToolSubsetAdmissionRequest",
    "admit_tool_subset",
]

ToolSubsetAdmissionRefusalReason = Literal[
    "EG_ASSEMBLE_UNAVAILABLE",
    "ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET",
]


class ToolSubsetAdmissionRequest(_WireModel):
    """A caller's request to admit the smallest authorized tool subset."""

    agent_graph_ref: str = Field(min_length=1, max_length=512)
    context_budget: A2AContextBudget


class ToolSubsetAdmissionDecision(_WireModel):
    """The door's admit/refuse result. Never advisory; never partial."""

    admitted: bool
    admitted_tools: tuple[str, ...] = Field(default=(), max_length=64)
    refusal_reason: ToolSubsetAdmissionRefusalReason | None = None
    provenance: str = Field(min_length=1, max_length=256)


def admit_tool_subset(
    request: ToolSubsetAdmissionRequest,
) -> ToolSubsetAdmissionDecision:
    """Admit the request, or fail closed with a typed, provenanced refusal.

    ``GRAPHOS-A2A-R002.1``: epistemic-graph's ``AgentAssemble`` operation
    returns unavailable today, so no context-budget-bound tool subset can be
    derived or cryptographically carried. This refuses closed rather than
    returning an advisory, cached, or partial subset.
    """

    del request
    return ToolSubsetAdmissionDecision(
        admitted=False,
        admitted_tools=(),
        refusal_reason="EG_ASSEMBLE_UNAVAILABLE",
        provenance="eg.AgentAssemble:unavailable",
    )
