"""Layered agent-capability model and harness abstraction (GRAPHOS-HOST-R015).

Defines GraphOS's ordered agent-capability layers, from its lowest defined
layer to its highest, and a harness abstraction so a given layer's
conformance can be exercised under different execution environments (an
in-process harness today; a remote or sandboxed harness later) without
changing the layer's defined behavior boundary.

This is the typed-model slice (R015 ``.1``): a minimal, self-contained
``InProcessHarness`` and the refusal rule that a harness never runs a layer
it has not declared support for. Further harnesses and layer-specific
conformance suites are follow-up work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class CapabilityLayer(IntEnum):
    """GraphOS's ordered agent-capability layers, lowest to highest."""

    SINGLE_TOOL_CALL = 0
    """One bounded tool invocation with a verified result, no planning."""

    SINGLE_AGENT_TASK = 1
    """One agent executes a bounded task loop over a fixed tool set."""

    MULTI_STEP_PLANNING = 2
    """One agent plans and executes a sequence of steps toward a goal."""

    MULTI_AGENT_ORCHESTRATION = 3
    """Multiple agents collaborate under one orchestrating control loop."""

    AUTONOMOUS_WORKFLOW = 4
    """A long-running, self-supervising workflow spanning many invocations."""


class UnsupportedCapabilityLayerError(RuntimeError):
    """A harness was asked to run a layer it does not declare support for."""


@dataclass(frozen=True)
class LayerRun:
    """The bounded result of running one capability layer through a harness."""

    layer: CapabilityLayer
    harness_name: str
    result: Any


@dataclass(frozen=True)
class InProcessHarness:
    """Runs a declared set of capability layers in the current process.

    ``handlers`` maps each supported layer to a zero-argument callable that
    performs that layer's bounded unit of work and returns its result.
    """

    name: str
    handlers: dict[CapabilityLayer, Callable[[], Any]] = field(default_factory=dict)

    @property
    def supported_layers(self) -> frozenset[CapabilityLayer]:
        return frozenset(self.handlers)

    def run(self, layer: CapabilityLayer) -> LayerRun:
        handler = self.handlers.get(layer)
        if handler is None:
            raise UnsupportedCapabilityLayerError(
                f"harness {self.name!r} does not support {layer.name} "
                f"(supports: {sorted(ly.name for ly in self.supported_layers)})"
            )
        return LayerRun(layer=layer, harness_name=self.name, result=handler())


def main() -> int:
    """CLI entry point: list the defined capability layers, lowest to highest."""
    for layer in sorted(CapabilityLayer):
        print(f"{layer.value}: {layer.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
