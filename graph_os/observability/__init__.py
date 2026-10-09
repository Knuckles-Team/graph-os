"""graph_os.observability — shared health-intelligence kernels.

CONCEPT:AU-OS.observability.unified-health-kernels (GRAPHOS-HOST-R008 slice:
``agent_utilities.observability.health`` ported verbatim). Metric-agnostic
anomaly-detection reasoning (distill → baseline → detect → correlate) that
any graph-os telemetry producer (e.g. ``graph_os.observability.gateway_health``,
ported in GRAPHOS-HOST-R008's prior slice) can wire a signal into. KG I/O
stays with ``agent_utilities.observability.health_ingest`` until that module
moves too; this package is reason-only, same as its AU predecessor.
"""

from graph_os.observability.health import (
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
]
