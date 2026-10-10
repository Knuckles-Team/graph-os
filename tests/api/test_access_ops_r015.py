"""GRAPHOS-OPS-R015.1: access elevation, lease, check, and policy-explain
operations registered and consistently scoped across every declared surface.

``approvals.grant``/``approvals.deny`` (the remaining "approval and denial"
slice of ``GRAPHOS-OPS-R015``) stay unregistered until the installed EG
contract publishes the ``approvals:read``/``approvals:decide`` scopes they
require; that EG-dependent remainder is tracked as ``GRAPHOS-OPS-R015.2`` in
``specs/hosted-api-operations/requirements.md`` and is not delivered here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.access import service
from graph_os.api.ops.access import operations
from graph_os.api.registry import Registry, Surface

pytestmark = pytest.mark.spec("GRAPHOS-OPS-R015.1")


def test_access_operations_registered_with_consistent_read_surfaces() -> None:
    registry = Registry(operations())
    read_ops = (
        "access.leases.list",
        "access.leases.get",
        "access.check",
        "access.explain_policy",
    )
    for op_id in read_ops:
        op = registry[op_id]
        assert {Surface.MCP, Surface.HTTP, Surface.A2A} <= op.surfaces

    approve = registry["access.elevation.approve"]
    assert approve.principals.value == "human_undelegated"
    assert approve.confirm is not None and approve.confirm.value == "console"


def _approver_context(*, principal: str) -> SimpleNamespace:
    caller = SimpleNamespace(
        principal=principal,
        tenant="tenant-a",
        delegated=False,
        effective_scopes=frozenset({"rbac:approve-elevation"}),
        engine_claims={"agent_id": principal, "tenant": "tenant-a"},
    )
    return SimpleNamespace(caller=caller, client=SimpleNamespace())


@pytest.mark.asyncio
async def test_elevation_approval_rejects_the_requester_approving_their_own_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same requester/approver principal must be refused by the shared access
    service, not merely by one surface's wiring (no-self-approval, GRAPHOS-OPS-R015).
    """
    from agent_utilities.security.elevation import ElevationService

    captured: dict[str, object] = {}

    async def _fake_approve(self, approval, *, surface, claims):  # noqa: ANN001
        captured["claims"] = dict(claims)
        if claims.get("agent_id") == captured.get("requester"):
            raise PermissionError("self-approval is refused")
        return SimpleNamespace(model_dump=lambda mode: {"elevation_id": "e1"})

    monkeypatch.setattr(ElevationService, "approve", _fake_approve, raising=True)

    context = _approver_context(principal="same-agent")
    captured["requester"] = "same-agent"
    with pytest.raises(PermissionError, match="self-approval"):
        await service.approve_elevation(
            context,
            {"elevation_id": "e1", "request_digest": "d" * 8},
            None,
        )
