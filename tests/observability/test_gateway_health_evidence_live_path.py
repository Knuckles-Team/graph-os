"""Live-path proof for the gateway health kernel wiring
(CONCEPT:AU-OS.observability.unified-health-kernels /
AU-KG.identity.evidence-spine-convergence, Evidence seam completion).

Ported from the agent runtime's
``tests/unit/observability/test_gateway_health_evidence_live_path.py``
(GRAPHOS-HOST-R008). The agent runtime's fifth test there
(``test_middleware_feeds_the_real_duration_into_record_request_duration``)
proved ``agent_utilities.observability.gateway_metrics``' middleware calls
this module's entry point — that module has not moved to graph-os yet, so
that specific integration test stays in the agent runtime (it now proves
the opposite: that the middleware degrades gracefully with this module
absent there). Every test that exercises `graph_os.observability.gateway_health`
itself, independent of the agent runtime's middleware, ported unchanged.

Two gaps closed by ONE producer (``graph_os/observability/gateway_health.py``):

1. ``agent_utilities.observability.health``'s anomaly-detection kernel +
   ``health_ingest.ingest_health_anomaly`` had ZERO live callers anywhere —
   a fully built, generalized-but-unwired kernel. This test proves a REAL
   caller exists.
2. ``MediaStore.store_metric_window_evidence`` (``MetricWindow`` locus) had no
   producer. This test proves an anomaly's own triggering trend window is now
   written as its evidence.

Offline throughout: health_ingest/media_store are both faked; no real engine.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from agent_utilities.observability import health_ingest

from graph_os.observability import gateway_health as gh
from graph_os.observability import health as h


class _FakeStore:
    def __init__(self) -> None:
        self.window_calls: list[tuple[bytes, dict]] = []

    def store_metric_window_evidence(self, data: bytes, **kwargs):
        self.window_calls.append((data, kwargs))
        return object()


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(gh, "_buffer", None)
    monkeypatch.setattr(gh, "_history", [])
    yield


def _normal_trend(avg: float, t0: float) -> dict:
    return {
        "min": avg - 0.01,
        "max": avg + 0.01,
        "avg": avg,
        "avg_control": None,
        "samples": 10,
        "window_s": gh._WINDOW_S,
        "start_at": t0,
        "end_at": t0 + gh._WINDOW_S,
    }


@pytest.mark.asyncio
async def test_check_and_record_writes_anomaly_and_metric_window_evidence(
    monkeypatch,
):
    # Seed six normal baseline windows (min_windows default) around ~0.05s latency.
    monkeypatch.setattr(
        gh,
        "_history",
        [_normal_trend(0.05, 1_000_000.0 + i * gh._WINDOW_S) for i in range(6)],
    )

    calls: list[tuple[str, str, dict, str]] = []

    def _fake_ingest_health_anomaly(entity_id, signal, anomaly, *, entity_type):
        calls.append((entity_id, signal, dict(anomaly), entity_type))
        return {"nodes": 1}

    monkeypatch.setattr(
        health_ingest, "ingest_health_anomaly", _fake_ingest_health_anomaly
    )

    store = _FakeStore()
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.memory.native_ingest.media_store",
        lambda: store,
    )

    # A genuinely anomalous window: latency spiked to 5s.
    anomalous_trend = _normal_trend(5.0, 1_000_000.0 + 6 * gh._WINDOW_S)

    await gh._check_and_record(anomalous_trend)

    # 1. The dead kernel's first live caller — a real :HealthAnomaly write.
    assert len(calls) == 1
    entity_id, signal, anomaly, entity_type = calls[0]
    assert entity_id == "gateway"
    assert signal == "request_duration_seconds"
    assert entity_type == "Gateway"
    assert anomaly["kind"] == "above-baseline"
    assert anomaly["observed"] == 5.0

    # 2. The MetricWindow evidence locus — the EXACT triggering window.
    assert len(store.window_calls) == 1
    data, kw = store.window_calls[0]
    assert data == json.dumps(anomalous_trend, sort_keys=True).encode("utf-8")
    assert kw["metric"] == "gateway:request_duration_seconds"
    assert kw["start_ms"] == int(anomalous_trend["start_at"] * 1000)
    assert kw["end_ms"] == int(anomalous_trend["end_at"] * 1000)
    assert kw["source"] == "gateway-health"

    # History grew by exactly the window just checked.
    assert gh._history[-1] == anomalous_trend


@pytest.mark.asyncio
async def test_check_and_record_writes_nothing_when_normal(monkeypatch):
    monkeypatch.setattr(
        gh,
        "_history",
        [_normal_trend(0.05, 1_000_000.0 + i * gh._WINDOW_S) for i in range(6)],
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        health_ingest,
        "ingest_health_anomaly",
        lambda *a, **k: calls.append((a, k)),
    )
    store = _FakeStore()
    monkeypatch.setattr(
        "agent_utilities.knowledge_graph.memory.native_ingest.media_store",
        lambda: store,
    )

    normal_trend = _normal_trend(0.051, 1_000_000.0 + 6 * gh._WINDOW_S)
    await gh._check_and_record(normal_trend)

    assert calls == []
    assert store.window_calls == []
    # Still folded into history for future baselines.
    assert gh._history[-1] == normal_trend


@pytest.mark.asyncio
async def test_check_and_record_skips_before_enough_history(monkeypatch):
    """Fewer than DEFAULT_MIN_WINDOWS prior windows -> no baseline -> no check."""
    monkeypatch.setattr(gh, "_history", [_normal_trend(0.05, 1_000_000.0)])
    calls: list[tuple] = []
    monkeypatch.setattr(
        health_ingest,
        "ingest_health_anomaly",
        lambda *a, **k: calls.append((a, k)),
    )
    await gh._check_and_record(_normal_trend(5.0, 1_000_100.0))
    assert calls == []


def test_record_request_duration_flush_triggers_the_check(monkeypatch):
    """The REAL entry point: enough `at=` samples to cross the window boundary
    schedules the background anomaly-check — proving the buffer -> flush ->
    check wiring, not just `_check_and_record` in isolation.
    """
    scheduled: list[dict] = []

    async def _fake_check(trend):
        scheduled.append(trend)

    monkeypatch.setattr(gh, "_check_and_record", _fake_check)

    import time as _time

    async def _drive():
        # First call lazily creates + caches the module-level buffer, seeded
        # from the real wall clock (`record_request_duration` always samples
        # `time.time()` — no injected clock, matching the real request path).
        gh.record_request_duration(0.05)
        buf = gh._buffer
        assert buf is not None
        # Still well inside the window -> no flush, nothing scheduled.
        gh.record_request_duration(0.06)
        assert scheduled == []

        # Push the buffer's own clock back past the window boundary — the
        # SAME "time elapsed since last flush" check the real buffer runs.
        buf._last_flush = _time.time() - gh._WINDOW_S - 1
        gh.record_request_duration(0.07)
        await asyncio.sleep(0)  # let the scheduled background task run
        assert len(scheduled) == 1
        assert scheduled[0]["samples"] >= 1

    asyncio.run(_drive())


def test_health_trend_buffer_flush_reports_real_sample_bounds():
    """`HealthTrendBuffer._flush` also carries the REAL min/max sample
    timestamps (`start_at`/`end_at`) `gateway_health` depends on — additive,
    proven directly against the shared kernel it wires.
    """
    buf = h.HealthTrendBuffer(window_s=100, max_samples=5000)
    t0 = 42.0
    assert buf.add(1.0, at=t0) is None
    assert buf.add(2.0, at=t0 + 5) is None
    out = buf.add(3.0, at=t0 + 101)
    assert out is not None
    assert out["start_at"] == t0
    assert out["end_at"] == t0 + 101
