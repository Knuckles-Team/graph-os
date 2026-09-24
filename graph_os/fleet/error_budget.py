"""Per-child error-budget windows from the live Prometheus facts (EH-406).

Each fleet child's calls are counted by outcome on
``agent_utilities_mcp_child_calls_total{server,outcome}`` (the multiplexer's
own counter, scraped by the deployment's Prometheus). One window is the
``increase`` of that counter over :data:`WINDOW_S`:

* **requests** -- calls that reached the child: ``ok``, ``error`` (the child
  answered with a tool error), ``transport_error`` and ``timeout``;
* **errors** -- the child failing to answer: ``transport_error`` and
  ``timeout`` (the fleet's 5XX).

Calls GraphOS itself refused (``busy``, ``short_circuited``, ``unavailable``)
are neither: counting its own shedding as errors would make a throttle feed on
itself. The queries are trusted definitions reached through AU's bounded,
allowlisted ``PrometheusHttpProvider``; no caller supplies PromQL. A child with
a missing or stale sample has no window (no evidence either way).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "BUDGET_SIGNALS",
    "WINDOW_S",
    "ChildWindow",
    "PrometheusErrorBudget",
    "prometheus_error_budget",
]

#: The observation window (seconds); the controller steps once per window.
WINDOW_S = 60
_CALLS = "agent_utilities_mcp_child_calls_total"
_ANSWERED = "ok|error|transport_error|timeout"
_FAILED = "transport_error|timeout"
_SIGNALS: dict[str, str] = {
    "child_window_requests": (
        f'sum(increase({_CALLS}{{server="{{service}}",outcome=~"{_ANSWERED}"}}'
        f"[{WINDOW_S}s])) or vector(0)"
    ),
    "child_window_errors": (
        f'sum(increase({_CALLS}{{server="{{service}}",outcome=~"{_FAILED}"}}'
        f"[{WINDOW_S}s])) or vector(0)"
    ),
}
BUDGET_SIGNALS = tuple(_SIGNALS)
_SCOPE = "fleet-child-error-budget"
#: Two signals per child; AU's provider bounds one batch at 64 queries.
_CHILDREN_PER_BATCH = 32


@dataclass(frozen=True, slots=True)
class ChildWindow:
    """One child's error-budget facts for the window ending at ``window_end_ms``."""

    child: str
    requests: int
    errors: int
    window_end_ms: int

    def sample(self) -> dict[str, int]:
        """EG's ``ErrorBudgetSample`` body."""
        return {
            "requests": self.requests,
            "errors": self.errors,
            "window_end_ms": self.window_end_ms,
        }


def _definitions() -> dict[str, Any]:
    from agent_utilities.orchestration.scaling_signals import SignalDefinition

    return {
        name: SignalDefinition(
            name=name,
            aggregation="fleet_total",
            query_template=query,
            unit="calls",
            scope=_SCOPE,
        )
        for name, query in _SIGNALS.items()
    }


def _count(value: Any) -> int | None:
    number = getattr(value, "value", None)
    if not isinstance(number, int | float) or not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _window(
    child: str, samples: Mapping[tuple[str, str], Any], window_end_ms: int
) -> ChildWindow | None:
    requests = _count(samples.get((child, "child_window_requests")))
    errors = _count(samples.get((child, "child_window_errors")))
    if requests is None or errors is None:
        return None
    return ChildWindow(child, requests, min(errors, requests), window_end_ms)


class PrometheusErrorBudget:
    """The live window source over a bounded signal provider."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def _read(self, children: Sequence[str]) -> dict[tuple[str, str], Any]:
        samples: dict[tuple[str, str], Any] = {}
        for start in range(0, len(children), _CHILDREN_PER_BATCH):
            batch = children[start : start + _CHILDREN_PER_BATCH]
            requests = [(child, signal) for child in batch for signal in BUDGET_SIGNALS]
            samples.update(self._provider.signal_values(requests))
        return samples

    async def windows(
        self, children: Sequence[str], window_end_ms: int
    ) -> list[ChildWindow]:
        """The observed windows; an unobserved child is simply absent."""
        samples = await asyncio.to_thread(self._read, list(children))
        found = (_window(child, samples, window_end_ms) for child in children)
        return [window for window in found if window is not None]


def prometheus_error_budget(
    *, base_url: str | None = None, transport: Any = None
) -> PrometheusErrorBudget | None:
    """The deployment's Prometheus source, or ``None`` when none is configured.

    ``base_url`` defaults to AU's ``SCALING_PROMETHEUS_URL`` (the Prometheus the
    fleet autoscaler and training admission already read).
    """
    from agent_utilities.core.config import config
    from agent_utilities.orchestration.scaling_signals import PrometheusHttpProvider

    url = base_url or config.scaling_prometheus_url
    if not url:
        return None
    return PrometheusErrorBudget(
        PrometheusHttpProvider(
            url, transport=transport, signal_definitions=_definitions()
        )
    )
