"""Arbora's own observability modules (ARBORA-HOST-R008).

Hosts the gateway-health producer and the shared health-intelligence kernels
moved from the agent runtime (CONCEPT:AU-OS.observability.unified-health-kernels,
ported verbatim). Metric-agnostic
anomaly-detection reasoning (distill → baseline → detect → correlate) that any
arbora telemetry producer (e.g. ``arbora.observability.gateway_health``)
can wire a signal into. The knowledge engine owns anomaly and incident
records; this package is reason-only.
The gateway metrics, the runtime health core, the trace export setup and the
correlation context live here too (ARBORA-HOST-R008).
"""

from __future__ import annotations

from . import gateway_health
from .health import (
    DEFAULT_MIN_WINDOWS,
    DEFAULT_SATURATED_CONTROL,
    DEFAULT_Z_THRESH,
    HealthTrendBuffer,
    compute_baseline,
    correlate,
    detect_anomaly,
)

__all__ = [
    "DEFAULT_MIN_WINDOWS",
    "DEFAULT_SATURATED_CONTROL",
    "DEFAULT_Z_THRESH",
    "HealthTrendBuffer",
    "compute_baseline",
    "correlate",
    "detect_anomaly",
    "gateway_health",
]
