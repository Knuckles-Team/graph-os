"""Fail-closed tests for the GRAPHOS-A2A-002 tool-subset admission door."""

from __future__ import annotations

import pytest

from graph_os.a2a.admission import (
    ToolSubsetAdmissionDecision,
    ToolSubsetAdmissionRequest,
    admit_tool_subset,
)
from graph_os.a2a.models import A2AContextBudget


@pytest.mark.spec("GRAPHOS-A2A-R016.1")
def test_admission_fails_closed_while_eg_assemble_is_unavailable() -> None:
    """GRAPHOS-A2A-R016.1: the exact refusal shape while upstream is down."""

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


@pytest.mark.spec("GRAPHOS-A2A-R019")
def test_admission_decision_always_carries_provenance_and_typed_reason() -> None:
    """GRAPHOS-A2A-R019: every decision is auditable, never bare."""

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


@pytest.mark.spec("GRAPHOS-A2A-R020")
def test_admission_never_returns_advisory_cached_or_partial_subset() -> None:
    """GRAPHOS-A2A-R020: an incomplete upstream answer is a refusal."""

    for ref, tokens in (("graph-a", 4_096), ("graph-a", 65_536), ("graph-b", 4_096)):
        decision = admit_tool_subset(
            ToolSubsetAdmissionRequest(
                agent_graph_ref=ref,
                context_budget=A2AContextBudget(tokens=tokens),
            )
        )

        assert decision.admitted is False
        assert decision.admitted_tools == ()
        assert decision.refusal_reason is not None
