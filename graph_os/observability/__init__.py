"""GraphOS's own observability modules (GRAPHOS-HOST-R008).

Hosts the gateway-health producer and the shared health-intelligence kernels
moved from the agent runtime (CONCEPT:AU-OS.observability.unified-health-kernels,
``agent_utilities.observability.health`` ported verbatim). Metric-agnostic
anomaly-detection reasoning (distill → baseline → detect → correlate) that any
graph-os telemetry producer (e.g. ``graph_os.observability.gateway_health``)
can wire a signal into. KG I/O stays with
``agent_utilities.observability.health_ingest`` until that module moves too;
this package is reason-only, same as its AU predecessor. Further
AU-BOUNDARY-R014 observability modules (``gateway_metrics``,
``runtime_health``, ``health_ingest``) land here incrementally, one
GRAPHOS-HOST-R008 slice at a time.
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
