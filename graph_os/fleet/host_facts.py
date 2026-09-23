"""Live host pressure and GPU capacity for policy-training admission (EH-347).

The default :class:`HostFacts` source reads the homelab's existing
Prometheus (``services/lgtm``) through AU's bounded, allowlisted
``PrometheusHttpProvider`` — the same symbolic-signal seam the fleet
autoscaler uses — instead of a second metrics client. Every signal is a
trusted definition keyed by the k8s node name (``nodename`` on node-exporter's
``node_uname_info``, ``node`` on kube-state-metrics), so no caller can submit
raw PromQL. A missing, stale or malformed sample leaves the host
*unobserved*, which admission refuses (fail closed).

Signals:

* inode use of ``/`` (node-exporter), available memory, free storage on ``/``;
* node pressure conditions (kube-state-metrics Memory/Disk/PID pressure);
* inference queue age: p95 vLLM request queue time on that host (0 when the
  host serves no vLLM queue);
* GPU memory: ``node_memory_MemTotal_bytes`` — correct for the unified-memory
  GB10; a discrete-GPU host must supply its capacity through
  ``capacity_overrides`` (there is no DCGM exporter in the stack).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

from graph_os.control_plane.policy_evolution import (
    HostCapacity,
    HostPressure,
    PolicyEvolutionControlError,
)

__all__ = [
    "HOST_SIGNALS",
    "PrometheusHostFacts",
    "clock_ms",
    "prometheus_host_facts",
]

_ON_NODE = (
    " * on(instance) group_left() max by (instance) "
    '(node_uname_info{nodename="{service}"})'
)
_PER_NODE: dict[str, tuple[str, str]] = {
    "host_inode_used_ppm": (
        "1000000 * (1 - max by (instance) (node_filesystem_files_free"
        '{mountpoint="/"}) / max by (instance) (node_filesystem_files'
        '{mountpoint="/"}))' + _ON_NODE,
        "ppm",
    ),
    "host_memory_available_bytes": (
        "max by (instance) (node_memory_MemAvailable_bytes)" + _ON_NODE,
        "bytes",
    ),
    "host_storage_free_bytes": (
        'max by (instance) (node_filesystem_avail_bytes{mountpoint="/"})' + _ON_NODE,
        "bytes",
    ),
    "host_gpu_memory_total_bytes": (
        "max by (instance) (node_memory_MemTotal_bytes)" + _ON_NODE,
        "bytes",
    ),
    "host_pod_pressure": (
        'max(kube_node_status_condition{node="{service}",'
        'condition=~"MemoryPressure|DiskPressure|PIDPressure",status="true"})',
        "flag",
    ),
    "host_queue_age_ms": (
        "(1000 * histogram_quantile(0.95, sum by (le) (rate("
        'vllm:request_queue_time_seconds_bucket{host="{service}"}[5m]))))'
        " or vector(0)",
        "ms",
    ),
}
HOST_SIGNALS = tuple(_PER_NODE)
_SCOPE = "policy-training-host"


def _definitions() -> dict[str, Any]:
    from agent_utilities.orchestration.scaling_signals import SignalDefinition

    return {
        name: SignalDefinition(
            name=name,
            aggregation="fleet_total",
            query_template=query,
            unit=unit,
            scope=_SCOPE,
        )
        for name, (query, unit) in _PER_NODE.items()
    }


class PrometheusHostFacts:
    """The live :class:`HostFacts` source over a bounded signal provider."""

    def __init__(
        self,
        provider: Any,
        host_ids: Sequence[str],
        capacity_overrides: Mapping[str, int] | None = None,
    ) -> None:
        self._provider = provider
        self._hosts = tuple(host_ids)
        self._overrides = dict(capacity_overrides or {})

    def _read(self) -> Mapping[tuple[str, str], Any]:
        requests = [(host, signal) for host in self._hosts for signal in HOST_SIGNALS]
        return self._provider.signal_values(requests)

    async def snapshot(
        self,
    ) -> tuple[Sequence[HostCapacity], dict[str, HostPressure]]:
        samples = await asyncio.to_thread(self._read)
        capacities: list[HostCapacity] = []
        pressure: dict[str, HostPressure] = {}
        for host in self._hosts:
            values = {
                signal: getattr(samples.get((host, signal)), "value", None)
                for signal in HOST_SIGNALS
            }
            total = self._overrides.get(host, values["host_gpu_memory_total_bytes"])
            if total is not None:
                capacities.append(
                    HostCapacity(host_id=host, gpu_memory_total_bytes=int(total))
                )
            observed = _pressure(host, values)
            if observed is not None:
                pressure[host] = observed
        return capacities, pressure


def _pressure(host: str, values: Mapping[str, float | None]) -> HostPressure | None:
    """One host's pressure, or ``None`` when any signal is unobserved."""
    if any(values[signal] is None for signal in HOST_SIGNALS):
        return None
    return HostPressure(
        host_id=host,
        inode_used_ppm=min(int(values["host_inode_used_ppm"] or 0), 1_000_000),
        memory_available_bytes=int(values["host_memory_available_bytes"] or 0),
        storage_free_bytes=int(values["host_storage_free_bytes"] or 0),
        queue_age_ms=int(values["host_queue_age_ms"] or 0),
        pod_pressure=bool(values["host_pod_pressure"]),
    )


def prometheus_host_facts(
    host_ids: Sequence[str],
    *,
    base_url: str | None = None,
    capacity_overrides: Mapping[str, int] | None = None,
    transport: Any = None,
) -> PrometheusHostFacts:
    """The default live source: the deployment's configured Prometheus.

    ``base_url`` defaults to AU's ``SCALING_PROMETHEUS_URL`` (the same
    Prometheus the fleet autoscaler reads); without one there is no live
    source and training admission is refused.
    """
    from agent_utilities.core.config import config
    from agent_utilities.orchestration.scaling_signals import PrometheusHttpProvider

    url = base_url or config.scaling_prometheus_url
    if not url:
        raise PolicyEvolutionControlError(
            "TRAINING_HOST_FACTS_UNAVAILABLE", "no Prometheus URL is configured"
        )
    provider = PrometheusHttpProvider(
        url, transport=transport, signal_definitions=_definitions()
    )
    return PrometheusHostFacts(provider, host_ids, capacity_overrides)


def clock_ms() -> int:
    """Wall-clock milliseconds for lease and ledger timestamps."""
    return time.time_ns() // 1_000_000
