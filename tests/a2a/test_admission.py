"""Fail-closed tests for the GRAPHOS-A2A-002 tool-subset admission door."""

from __future__ import annotations

from graph_os.a2a.admission import (
    ToolSubsetAdmissionDecision,
    ToolSubsetAdmissionRequest,
    admit_tool_subset,
)
from graph_os.a2a.models import A2AContextBudget


def test_admission_fails_closed_while_eg_assemble_is_unavailable() -> None:
    """GRAPHOS-A2A-002-R001.1: the exact refusal shape while upstream is down."""

    request = ToolSubsetAdmissionRequest(
        agent_graph_ref="agent-graph-ref-1",
        context_budget=A2AContextBudget(tokens=4_096),
    )

    decision = admit_tool_subset(request)

    assert isinstance(decision, ToolSubsetAdmissionDecision)
    assert decision.admitted is False
    assert decision.admitted_tools == ()
    assert decision.refusal_reason == "EG_ASSEMBLE_UNAVAILABLE"
    assert decision.provenance == "eg.AgentAssemble:unavailable"


def test_admission_decision_always_carries_provenance_and_typed_reason() -> None:
    """GRAPHOS-A2A-002-R004: every decision is auditable, never bare."""

    request = ToolSubsetAdmissionRequest(
        agent_graph_ref="agent-graph-ref-2",
        context_budget=A2AContextBudget(tokens=65_536),
    )

    decision = admit_tool_subset(request)

    assert decision.provenance
    if not decision.admitted:
        assert decision.refusal_reason in (
            "EG_ASSEMBLE_UNAVAILABLE",
            "ENVELOPE_LACKS_ALLOWED_TOOL_SUBSET",
        )
