"""GraphOS's own observability modules (GRAPHOS-HOST-R008).

Hosts the gateway-health producer moved from the agent runtime. Further
AU-BOUNDARY-R014 observability modules (``gateway_metrics``,
``runtime_health``, ``health``, ``health_ingest``) land here incrementally,
one GRAPHOS-HOST-R008 slice at a time.
"""

from __future__ import annotations

from . import gateway_health

__all__ = ["gateway_health"]
