"""Deploy-watch operation contract: shape, handler, and caller authorization.

Mirrors ``tests/api/test_agent_browser_ops.py``'s pattern for exercising a
fleet operation module directly against the shared registry chokepoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from graph_os.api.ops import fleet_deploy_watch
from graph_os.api.registry import Caller, Effect, Registry, Surface, Verb, authorized


@dataclass(frozen=True)
class _FakeCaller:
    """A concrete stand-in structurally satisfying ``graph_os.api.registry.Caller``."""

    effective_scopes: frozenset[str]
    principal_kind: str = "human"
    delegated: bool = False


def _allow_all(_op: object, _caller: Caller) -> bool:
    return True


def _deny_all(_op: object, _caller: Caller) -> bool:
    return False


@pytest.mark.spec("GRAPHOS-FLEET-R024.1")
def test_operation_is_a_read_ask_scoped_to_mcp_delegate() -> None:
    (op,) = fleet_deploy_watch.operations()
    assert op.id == "fleet.deploy_watch.evaluate"
    assert op.verb is Verb.ASK
    assert op.effect is Effect.READ
    assert op.scopes == frozenset({"mcp:delegate"})
    assert Surface.MCP in op.surfaces


@pytest.mark.spec("GRAPHOS-FLEET-R024.1")
def test_registry_rejects_a_caller_missing_the_fleet_scope() -> None:
    registry = Registry(fleet_deploy_watch.operations())
    op = registry["fleet.deploy_watch.evaluate"]
    unauthorized_caller = _FakeCaller(effective_scopes=frozenset({"kg:read"}))
    assert authorized(op, unauthorized_caller, policy=_allow_all) is False
    assert registry.find(unauthorized_caller, policy=_allow_all, verb=Verb.ASK) == ()


@pytest.mark.spec("GRAPHOS-FLEET-R024.1")
def test_registry_admits_a_caller_with_the_exact_fleet_scope() -> None:
    registry = Registry(fleet_deploy_watch.operations())
    op = registry["fleet.deploy_watch.evaluate"]
    caller = _FakeCaller(effective_scopes=frozenset({"mcp:delegate"}))
    assert authorized(op, caller, policy=_allow_all) is True
    assert registry.find(caller, policy=_allow_all, verb=Verb.ASK) == (op,)


def test_registry_still_refuses_a_scoped_caller_when_policy_denies() -> None:
    registry = Registry(fleet_deploy_watch.operations())
    op = registry["fleet.deploy_watch.evaluate"]
    caller = _FakeCaller(effective_scopes=frozenset({"mcp:delegate"}))
    assert authorized(op, caller, policy=_deny_all) is False


@pytest.mark.asyncio
async def test_handler_returns_the_verdict_for_an_authorized_call() -> None:
    (op,) = fleet_deploy_watch.operations()
    context = SimpleNamespace(caller=object(), services={})
    result = await fleet_deploy_watch.handle_fleet_deploy_watch_evaluate(
        context,
        {
            "service": "checkout-api",
            "probes": ({"status": "down", "detail": "503 from /healthz"},),
        },
        op,
    )
    assert result == {
        "outcome": "failed",
        "detail": "503 from /healthz",
        "healthy_probes": 0,
        "probes": 1,
    }
