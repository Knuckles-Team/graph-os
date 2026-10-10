"""Tests for the GRAPHOS-HOST-R015 layered agent-capability model."""

from __future__ import annotations

import pytest

from graph_os.architecture.capability_layers import (
    CapabilityLayer,
    InProcessHarness,
    UnsupportedCapabilityLayerError,
)


@pytest.mark.spec('GRAPHOS-HOST-R015')
def test_layers_are_ordered_lowest_to_highest() -> None:
    ordered = sorted(CapabilityLayer)
    assert ordered == [
        CapabilityLayer.SINGLE_TOOL_CALL,
        CapabilityLayer.SINGLE_AGENT_TASK,
        CapabilityLayer.MULTI_STEP_PLANNING,
        CapabilityLayer.MULTI_AGENT_ORCHESTRATION,
        CapabilityLayer.AUTONOMOUS_WORKFLOW,
    ]


@pytest.mark.spec('GRAPHOS-HOST-R015')
def test_harness_runs_a_layer_it_declares_support_for() -> None:
    harness = InProcessHarness(
        name="pilot",
        handlers={CapabilityLayer.SINGLE_TOOL_CALL: lambda: "ok"},
    )
    run = harness.run(CapabilityLayer.SINGLE_TOOL_CALL)
    assert run.result == "ok"
    assert run.harness_name == "pilot"
    assert run.layer is CapabilityLayer.SINGLE_TOOL_CALL


@pytest.mark.spec('GRAPHOS-HOST-R015')
def test_harness_refuses_a_layer_it_does_not_support() -> None:
    harness = InProcessHarness(
        name="pilot",
        handlers={CapabilityLayer.SINGLE_TOOL_CALL: lambda: "ok"},
    )
    with pytest.raises(UnsupportedCapabilityLayerError):
        harness.run(CapabilityLayer.MULTI_AGENT_ORCHESTRATION)


def test_supported_layers_reflects_declared_handlers_only() -> None:
    harness = InProcessHarness(
        name="pilot",
        handlers={
            CapabilityLayer.SINGLE_TOOL_CALL: lambda: None,
            CapabilityLayer.SINGLE_AGENT_TASK: lambda: None,
        },
    )
    assert harness.supported_layers == frozenset(
        {CapabilityLayer.SINGLE_TOOL_CALL, CapabilityLayer.SINGLE_AGENT_TASK}
    )
