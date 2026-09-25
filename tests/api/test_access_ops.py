"""Focused authority tests for access operation handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from graph_os.access import service


def _context(*, principal: str = "approver", delegated: bool = False):
    caller = SimpleNamespace(
        principal=principal,
        tenant="tenant-a",
        delegated=delegated,
        effective_scopes=frozenset({"rbac:approve-elevation"}),
        engine_claims={"agent_id": principal, "tenant": "tenant-a"},
    )
    leases = SimpleNamespace(get=AsyncMock(), list=AsyncMock(), transition=AsyncMock())
    return SimpleNamespace(caller=caller, client=SimpleNamespace(control_leases=leases))


async def test_action_approval_decision_uses_verified_tenant_and_revision() -> None:
    context = _context()
    lease = {"kind": service.ACTION_APPROVAL_KIND, "status": "active", "revision": 7}
    context.client.control_leases.get.return_value = lease
    context.client.control_leases.transition.return_value = {"outcome": "applied"}
    result = await service.grant_approval(
        context, {"approval_id": "action_approval:7"}, None
    )
    assert result == {"outcome": "applied"}
    context.client.control_leases.transition.assert_awaited_once_with(
        tenant="tenant-a",
        lease_id="action_approval:7",
        expected_revision=7,
        to="consumed",
        idempotency_key="decide:action_approval:7",
    )


async def test_action_approval_refuses_wrong_kind_before_transition() -> None:
    context = _context()
    context.client.control_leases.get.return_value = {
        "kind": "capacity.lease",
        "status": "active",
        "revision": 2,
    }
    with pytest.raises(LookupError):
        await service.deny_approval(context, {"approval_id": "action_approval:7"}, None)
    context.client.control_leases.transition.assert_not_awaited()


async def test_elevation_deny_does_not_disguise_revoke() -> None:
    with pytest.raises(NotImplementedError, match="ELEVATION_DENY_UNAVAILABLE"):
        await service.deny_elevation(_context(), {"elevation_id": "e"}, None)


async def test_lease_listing_uses_verified_tenant() -> None:
    context = _context()
    context.client.control_leases.list.return_value = {
        "leases": [],
        "next_cursor": None,
    }
    await service.list_leases(context, {"kind": "action.approval"}, None)
    context.client.control_leases.list.assert_awaited_once_with(
        tenant="tenant-a", kind="action.approval", status=None, cursor=None, limit=100
    )


def test_specs_require_console_confirmation_for_decisions() -> None:
    pytest.importorskip("graph_os.api.registry")
    from graph_os.api.ops.access import specs

    ops = {op.id: op for op in specs()}
    assert "access.elevation.deny" not in ops
    for op_id in ("access.elevation.approve", "approvals.grant", "approvals.deny"):
        op = ops[op_id]
        assert op.confirm.value == "console"
        assert op.principals.value == "human_undelegated"
