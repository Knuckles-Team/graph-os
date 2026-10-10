"""Focused contract for the GRAPHOS-CAPACITY-R002 hosted operations.

Mirrors ``tests/api/test_agent_browser_ops.py``'s direct-handler pattern:
operations are exercised through their declared handler, not the full
``get_registry()`` assembly (that coverage lives in
``tests/api/test_registry_factory.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.ops import capacity
from graph_os.api.registry import Effect, Registry, Surface
from graph_os.fleet.error_budget import (
    AimdConfig,
    BudgetWindow,
    OutcomeClass,
    OutcomeSample,
    Partition,
    ThrottleMode,
    decide,
)
from graph_os.fleet.throttle_service import ThrottleRegistry

pytestmark = pytest.mark.spec("GRAPHOS-CAPACITY-R002")

_PARTITION_PARAMS = {
    "tenant": "t1",
    "child": "search",
    "operation_class": "read",
    "policy_revision": "p1",
}


def _context(**services: object) -> SimpleNamespace:
    return SimpleNamespace(
        caller=SimpleNamespace(session=object()),
        client=object(),
        idempotency_key="key-1",
        services=services,
    )


def test_capacity_ops_declare_exact_scopes_and_admin_effect() -> None:
    registry = Registry(capacity.operations())
    assert len(registry) == 2
    status_op = registry["capacity.throttle.status"]
    mode_op = registry["capacity.throttle.set_mode"]
    assert status_op.scopes == frozenset({"capacity:read"})
    assert status_op.effect is Effect.READ
    assert mode_op.scopes == frozenset({"capacity:admin"})
    assert mode_op.effect is Effect.ADMIN
    assert Surface.MCP in status_op.surfaces
    assert all(op.examples for op in registry)


@pytest.mark.asyncio
async def test_status_handler_reports_unbound_controller_closed() -> None:
    op = capacity.operations()[0]
    with pytest.raises(RuntimeError, match="not bound"):
        await capacity.handle_capacity_status(_context(), _PARTITION_PARAMS, op)


@pytest.mark.asyncio
async def test_status_handler_reports_recorded_decision() -> None:
    registry = ThrottleRegistry()
    partition = Partition(**_PARTITION_PARAMS)
    window = BudgetWindow(
        window_id="w1",
        partition=partition,
        samples=tuple(OutcomeSample(OutcomeClass.TIMEOUT) for _ in range(8)),
    )
    config = AimdConfig(
        version="v1",
        alpha=2,
        beta=0.5,
        floor=1,
        min_sample_count=4,
        error_budget_fraction=0.2,
    )
    decision = decide(
        window=window,
        prior_limit=10,
        engine_headroom=20,
        config=config,
        mode=ThrottleMode.ENFORCE,
    )
    registry.record_decision(decision)

    op = capacity.operations()[0]
    result = await capacity.handle_capacity_status(
        _context(throttle_registry=registry), _PARTITION_PARAMS, op
    )
    assert result == {
        "value": {
            "mode": "observe",
            "current_limit": decision.new_limit,
            "reason": "budget_breach_decrease",
        }
    }


@pytest.mark.asyncio
async def test_set_mode_handler_updates_and_is_read_back() -> None:
    registry = ThrottleRegistry()
    op = capacity.operations()[1]
    params = {**_PARTITION_PARAMS, "mode": "enforce"}
    result = await capacity.handle_capacity_set_mode(
        _context(throttle_registry=registry), params, op
    )
    assert result == {"value": {"mode": "enforce"}}
    assert registry.mode(Partition(**_PARTITION_PARAMS)) is ThrottleMode.ENFORCE


@pytest.mark.asyncio
async def test_set_mode_handler_reports_unbound_controller_closed() -> None:
    op = capacity.operations()[1]
    params = {**_PARTITION_PARAMS, "mode": "enforce"}
    with pytest.raises(RuntimeError, match="not bound"):
        await capacity.handle_capacity_set_mode(_context(), params, op)
