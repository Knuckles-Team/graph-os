"""EH-406: fleet children are throttled on EG CapacityCells from live error budgets.

The Prometheus side runs AU's real ``PrometheusHttpProvider`` over an
``httpx.MockTransport``; the EG side is a fake capacity ledger that applies
EG's AIMD rule (narrow on a burst, recover by the step, never above the
declared capacity); the child side is a real ``ChildRuntime``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graph_os.fleet.child_resilience import ChildRuntime, MCPChildBusyError
from graph_os.fleet.error_budget import prometheus_error_budget
from graph_os.fleet.throttle_controller import (
    DEFAULT_POLICY,
    EVOLVE_EVERY,
    ThrottleController,
    ThrottleControllerExtension,
    declared_targets,
)
from graph_os.fleet.throttle_limiter import ResizableLimiter
from tests.fleet.test_multiplexer_resilience import GatedSession

POLICY = {
    "error_budget_ppm": 50_000,
    "recovery_ppm": 10_000,
    "min_samples": 10,
    "decrease_per_mille": 500,
    "increase_step": 1,
    "floor": 1,
    "cooldown_ms": 0,
}
DECLARED = {"capacity": 8, "policy": POLICY, "mode": "enforce"}


# --- the limiter -----------------------------------------------------------


async def test_a_narrowed_limiter_admits_nothing_new_until_calls_finish() -> None:
    limiter = ResizableLimiter(4)
    for _ in range(3):
        await limiter.acquire()
    limiter.resize(2)
    assert limiter.locked()
    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    limiter.release()
    await asyncio.sleep(0)
    assert not waiter.done(), "2 still held: at the narrowed limit"
    limiter.release()
    await asyncio.wait_for(waiter, 1)
    assert limiter.held == 2


async def test_widening_wakes_waiters_and_a_cancelled_waiter_leaks_nothing() -> None:
    limiter = ResizableLimiter(1)
    await limiter.acquire()
    gone = asyncio.create_task(limiter.acquire())
    kept = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    gone.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await gone
    limiter.resize(2)
    await asyncio.wait_for(kept, 1)
    assert limiter.held == 2


async def test_a_child_runtime_obeys_its_throttle_ceiling() -> None:
    session = GatedSession()
    runtime = ChildRuntime("github", {"max_concurrency": 4, "queue_timeout": 0.05})
    runtime.adopt_sessions([session])
    runtime.apply_throttle_ceiling(1)
    first = asyncio.create_task(runtime.call_tool("t", {}))
    await asyncio.sleep(0.01)
    with pytest.raises(MCPChildBusyError):
        await runtime.call_tool("t", {})
    assert runtime.status()["throttle_ceiling"] == 1
    runtime.apply_throttle_ceiling(99)
    second = asyncio.create_task(runtime.call_tool("t", {}))
    await asyncio.sleep(0.01)
    assert session.active == 2, "the ceiling never lifts past max_concurrency"
    session.release.set()
    await asyncio.gather(first, second)


# --- declarations ------------------------------------------------------------


def test_every_child_is_observed_and_only_an_opt_in_is_enforced(caplog: Any) -> None:
    children = {
        "plain": SimpleNamespace(cfg={}, max_concurrency=6),
        "github": SimpleNamespace(cfg={"error_budget": DECLARED}),
        "typo": SimpleNamespace(
            cfg={"error_budget": {**DECLARED, "policy": {**POLICY, "floor": 0}}},
            max_concurrency=4,
        ),
    }
    targets = {t.child: t.declaration for t in declared_targets(children)}
    assert {name: d.mode.value for name, d in targets.items()} == {
        "github": "enforce",
        "plain": "observe",
        "typo": "observe",
    }
    assert targets["plain"].capacity == 6
    assert targets["plain"].policy.model_dump() == DEFAULT_POLICY
    assert "typo declares an invalid error_budget" in caplog.text


# --- the controller over a fake EG ledger ------------------------------------


@dataclass
class FakeCapacity:
    cells: dict[str, dict[str, Any]] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)

    async def status(self, request: dict[str, Any]) -> dict[str, Any]:
        cell = self.cells.get(request["cell_id"])
        return {"schema_version": "1", "cells": [cell] if cell else [], "leases": []}

    async def update_cell(self, request: dict[str, Any]) -> dict[str, Any]:
        cell = request["cell"]
        current = self.cells.get(cell["cell_id"])
        expected = None if current is None else current["epoch"]
        if request["expected_epoch"] != expected:
            return {"schema_version": "1", "decision": "stale_epoch"}
        self.updates.append(request)
        self.cells[cell["cell_id"]] = cell
        return {"schema_version": "1", "decision": "accepted"}


@dataclass
class FakeEg:
    capacity_leases: FakeCapacity = field(default_factory=FakeCapacity)
    samples: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    async def _send(
        self, method: str, params: dict[str, Any], graph: Any, **_: Any
    ) -> Any:
        assert method == "ThrottleCapacityCell"
        if self.down:
            raise ConnectionError("engine unavailable")
        request = params["request"]
        self.samples.append(request["sample"])
        cell = self.capacity_leases.cells[request["cell_id"]]
        throttle, sample = cell["throttle"], request["sample"]
        policy, before = throttle["policy"], throttle["ceiling"]
        ppm = 1_000_000 * sample["errors"] // max(sample["requests"], 1)
        if sample["requests"] < policy["min_samples"]:
            action = "held"
        elif ppm > policy["error_budget_ppm"]:
            throttle["ceiling"] = max(
                policy["floor"], before * policy["decrease_per_mille"] // 1000
            )
            action = "narrowed"
        else:
            throttle["ceiling"] = min(
                cell["capacity"], before + policy["increase_step"]
            )
            action = "recovered"
        record = {
            "action": action,
            "reason": "test",
            "from": before,
            "to": throttle["ceiling"],
        }
        return {"schema_version": "1", "cell": cell, "action": record}


def _prometheus(windows: dict[str, tuple[int, int]]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlsplit(str(request.url)).query)["query"][0]
        child = query.split('server="')[1].split('"')[0]
        requests, errors = windows[child]
        value = (
            errors
            if 'transport_error|timeout"' in query and "ok|" not in query
            else requests
        )
        result = [{"metric": {}, "value": [time.time(), str(value)]}]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": result},
            },
        )

    return httpx.MockTransport(handle)


def _controller(
    eg: FakeEg, windows: dict[str, tuple[int, int]], children: dict[str, Any], **kw: Any
) -> ThrottleController:
    source = prometheus_error_budget(
        base_url="http://prometheus.test", transport=_prometheus(windows)
    )

    @contextlib.contextmanager
    def authority() -> Any:
        yield eg

    return ThrottleController(
        children=lambda: children,
        source=source,
        authority=authority,
        tenant_ref="graph-os",
        **kw,
    )


def _child(declaration: dict[str, Any] = DECLARED) -> Any:
    applied: list[int | None] = []
    return SimpleNamespace(
        cfg={"error_budget": declaration},
        applied=applied,
        apply_throttle_ceiling=applied.append,
    )


async def test_an_error_burst_narrows_the_child_and_health_recovers_it() -> None:
    eg, child = FakeEg(), _child()
    windows = {"github": (100, 40)}
    controller = _controller(eg, windows, {"github": child})
    reports = await controller.step()
    assert reports == [
        {
            "child": "github",
            "mode": "enforce",
            "outcome": "narrowed",
            "reason": "test",
            "ceiling": 4,
        }
    ]
    assert eg.samples[0]["requests"] == 100 and eg.samples[0]["errors"] == 40
    cell = eg.capacity_leases.cells["fleet/child/github"]
    assert cell["capacity"] == 8 and cell["throttle"]["policy"] == POLICY
    windows["github"] = (100, 0)
    await controller.step()
    assert child.applied == [4, 5], "recovery is additive and evidence-gated"
    assert len(eg.capacity_leases.updates) == 1, "declared once"


async def test_a_failed_step_keeps_the_last_ceiling() -> None:
    eg, child = FakeEg(), _child()
    controller = _controller(eg, {"github": (100, 40)}, {"github": child})
    await controller.step()
    eg.down = True
    reports = await controller.step()
    assert reports == [{"child": "github", "outcome": "failed"}]
    assert child.applied == [4], "nothing widened while EG was unreachable"


async def test_a_changed_declaration_is_redeclared_keeping_the_narrowing() -> None:
    eg, child = FakeEg(), _child()
    children = {"github": child}
    controller = _controller(eg, {"github": (100, 40)}, children)
    await controller.step()
    children["github"] = _child({**DECLARED, "capacity": 6})
    children["github"].applied.extend(child.applied)
    await controller.step()
    cell = eg.capacity_leases.cells["fleet/child/github"]
    assert cell["capacity"] == 6 and cell["epoch"] == 2
    assert eg.capacity_leases.updates[1]["expected_epoch"] == 1


async def test_bounded_children_get_a_guardrail_evolution_step() -> None:
    eg, child = FakeEg(), _child()
    evolved: list[str] = []

    class Evolution:
        async def evolve(self, cell_id: str, bounds: Any) -> Any:
            evolved.append(cell_id)
            return SimpleNamespace(outcome=SimpleNamespace(value="held"))

    ladder = {
        "loosest": {
            k: POLICY[k]
            for k in (
                "error_budget_ppm",
                "recovery_ppm",
                "decrease_per_mille",
                "increase_step",
            )
        },
        "tightest": {
            "error_budget_ppm": 10_000,
            "recovery_ppm": 2_000,
            "decrease_per_mille": 300,
            "increase_step": 1,
        },
        "levels": 3,
    }
    bounded = _child({**DECLARED, "bounds": ladder})
    controller = _controller(
        eg,
        {"github": (0, 0), "bounded": (0, 0)},
        {"github": child, "bounded": bounded},
        evolution=lambda client: Evolution(),
    )
    for _ in range(EVOLVE_EVERY):
        reports = await controller.step()
    assert evolved == ["fleet/child/bounded"]
    assert {"child": "bounded", "evolution": "held"} in reports


# --- observe mode (operator ruling 2026-09-24) --------------------------------


def _observed_runtime(name: str = "github") -> ChildRuntime:
    return ChildRuntime(name, {"max_concurrency": 8, "queue_timeout": 0.05})


def _gauge(child: str, mode: str) -> float | None:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(
        "agent_utilities_mcp_child_throttle_ceiling",
        {"server": child, "mode": mode},
    )


async def test_observe_mode_computes_and_exports_but_never_touches_the_limiter() -> (
    None
):
    eg, runtime = FakeEg(), _observed_runtime()
    windows = {"github": (100, 60)}
    controller = _controller(eg, windows, {"github": runtime})
    for _ in range(3):
        reports = await controller.step()
    assert reports[0]["mode"] == "observe" and reports[0]["ceiling"] == 1
    assert _gauge("github", "observe") == 1.0, "the would-be ceiling is exported"
    limiter = runtime._semaphore
    assert limiter is not None and limiter.limit == 8, "admission never narrowed"
    assert runtime.throttle_ceiling is None
    cell = eg.capacity_leases.cells["fleet/child/github"]
    assert cell["capacity"] == 8, "default budget = the child's max_concurrency"


async def test_switching_back_to_observe_lifts_an_enforced_ceiling() -> None:
    eg, runtime = FakeEg(), _observed_runtime()
    runtime.cfg["error_budget"] = DECLARED
    controller = _controller(eg, {"github": (100, 40)}, {"github": runtime})
    await controller.step()
    assert runtime._semaphore is not None and runtime._semaphore.limit == 4
    runtime.cfg["error_budget"] = {**DECLARED, "mode": "observe"}
    await controller.step()
    assert runtime._semaphore.limit == 8 and runtime.throttle_ceiling is None


async def test_observed_children_get_no_evolution_step() -> None:
    eg, runtime = FakeEg(), _observed_runtime()
    evolved: list[str] = []

    class Evolution:
        async def evolve(self, cell_id: str, bounds: Any) -> Any:
            evolved.append(cell_id)
            return SimpleNamespace(outcome=SimpleNamespace(value="held"))

    controller = _controller(
        eg, {"github": (0, 0)}, {"github": runtime}, evolution=lambda c: Evolution()
    )
    for _ in range(EVOLVE_EVERY):
        await controller.step()
    assert evolved == []


def test_without_a_prometheus_url_nothing_is_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.core.config import config

    monkeypatch.setattr(config, "scaling_prometheus_url", None)
    assert prometheus_error_budget() is None


async def test_the_extension_runs_the_controller_for_the_server_lifetime() -> None:
    steps: list[int] = []

    class Controller(ThrottleController):
        async def run(self) -> None:
            steps.append(1)
            await asyncio.Event().wait()

    controller = Controller(
        children=dict, source=None, authority=contextlib.nullcontext, tenant_ref="t"
    )
    async with ThrottleControllerExtension(controller).lifespan():
        await asyncio.sleep(0)
        assert steps == [1]
