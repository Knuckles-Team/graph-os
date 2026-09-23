"""Live Prometheus host facts for policy-training admission (EH-347).

The real AU ``PrometheusHttpProvider`` runs against an ``httpx.MockTransport``
standing in for the homelab Prometheus, so the symbolic signal definitions,
query construction, freshness validation and host projection are all real.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graph_os.control_plane.policy_evolution import PolicyEvolutionControlError
from graph_os.fleet.host_facts import HOST_SIGNALS, prometheus_host_facts

GIB = 1024**3

_VALUES = {
    "node_filesystem_files_free": 250_000.0,
    "node_memory_MemAvailable_bytes": 40 * GIB,
    "node_filesystem_avail_bytes": 300 * GIB,
    "node_memory_MemTotal_bytes": 128 * GIB,
    "kube_node_status_condition": 0.0,
    "vllm:request_queue_time_seconds_bucket": 1500.0,
}


def _value_for(query: str) -> float:
    for family, value in _VALUES.items():
        if family in query:
            return value
    raise AssertionError(query)


def _prometheus(seen: list[str], missing: str | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlsplit(str(request.url)).query)["query"][0]
        seen.append(query)
        result = (
            []
            if missing is not None and missing in query
            else [{"metric": {}, "value": [time.time(), str(_value_for(query))]}]
        )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": result},
            },
        )

    return httpx.MockTransport(handle)


async def test_live_metrics_become_host_capacity_and_pressure() -> None:
    seen: list[str] = []
    facts = prometheus_host_facts(
        ("gb10",), base_url="http://prometheus.test", transport=_prometheus(seen)
    )
    capacities, pressure = await facts.snapshot()
    assert [(c.host_id, c.gpu_memory_total_bytes) for c in capacities] == [
        ("gb10", 128 * GIB)
    ]
    observed = pressure["gb10"]
    assert observed.memory_available_bytes == 40 * GIB
    assert observed.storage_free_bytes == 300 * GIB
    assert observed.queue_age_ms == 1500 and observed.pod_pressure is False
    assert len(seen) == len(HOST_SIGNALS)
    assert all('"gb10"' in query for query in seen)


async def test_a_missing_signal_leaves_the_host_unobserved() -> None:
    facts = prometheus_host_facts(
        ("gb10",),
        base_url="http://prometheus.test",
        transport=_prometheus([], missing="kube_node_status_condition"),
    )
    _capacities, pressure = await facts.snapshot()
    assert "gb10" not in pressure


async def test_a_capacity_override_wins_for_discrete_gpu_hosts() -> None:
    facts = prometheus_host_facts(
        ("gb10",),
        base_url="http://prometheus.test",
        capacity_overrides={"gb10": 24 * GIB},
        transport=_prometheus([]),
    )
    capacities, _pressure = await facts.snapshot()
    assert capacities[0].gpu_memory_total_bytes == 24 * GIB


def test_no_configured_prometheus_is_a_typed_refusal(monkeypatch) -> None:
    from agent_utilities.core.config import config

    monkeypatch.setattr(config, "scaling_prometheus_url", None)
    with pytest.raises(PolicyEvolutionControlError) as refused:
        prometheus_host_facts(("gb10",))
    assert refused.value.code == "TRAINING_HOST_FACTS_UNAVAILABLE"
