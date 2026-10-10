"""GRAPHOS-OPS-R016.1: capacity throttle status/mode operations, bounded to
the observe/enforce enum under admin scope, as read through the hosted
operation registry.

Reading and setting the engine's own ``CapacityCell`` ceiling (the "capacity
status" and "cell update" slices of ``GRAPHOS-OPS-R016``) is not implemented
here: the pinned EG contract does not yet publish a capacity-cell method.
That EG-dependent remainder is tracked as ``GRAPHOS-OPS-R016.2`` in
``specs/hosted-api-operations/requirements.md``.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from graph_os.api.ops import capacity
from graph_os.api.registry import Effect, Registry, Surface
from graph_os.fleet.error_budget import ThrottleMode

pytestmark = pytest.mark.spec("GRAPHOS-OPS-R016.1")


def test_throttle_ops_registered_with_read_vs_admin_scope_split() -> None:
    registry = Registry(capacity.operations())
    status_op = registry["capacity.throttle.status"]
    mode_op = registry["capacity.throttle.set_mode"]
    assert status_op.scopes == frozenset({"capacity:read"})
    assert status_op.effect is Effect.READ
    assert mode_op.scopes == frozenset({"capacity:admin"})
    assert mode_op.effect is Effect.ADMIN
    assert Surface.MCP in status_op.surfaces
    assert Surface.MCP in mode_op.surfaces


def test_throttle_mode_is_bounded_to_the_observe_enforce_enum() -> None:
    with pytest.raises(ValidationError):
        capacity.CapacitySetModeParams(
            tenant="t1",
            child="search",
            operation_class="read",
            policy_revision="p1",
            mode=cast(ThrottleMode, "disabled"),
        )


def test_registry_factory_includes_capacity_in_curated_modules() -> None:
    import inspect

    import graph_os.api.ops.registry_factory as registry_factory_module

    source = inspect.getsource(registry_factory_module.get_registry)
    assert "capacity" in source
