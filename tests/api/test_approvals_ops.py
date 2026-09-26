"""MCPI-31 approval scope, tenancy, and fail-closed decision checks."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from graph_os.access.approvals import (
    ApprovalService,
    NativeLeaseDecisionAuthority,
    execute,
)
from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.ops.approvals import ApprovalDecisionParams, specs
from graph_os.api.registry import Confirm, PrincipalRule, Surface


class Leases:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        return {"leases": self.rows}

    async def get(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("get", kwargs))
        return next(
            (row for row in self.rows if row["lease_id"] == kwargs["lease_id"]),
            None,
        )

    async def transition(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("transition", kwargs))
        raise AssertionError("unbound native transition must not decide approvals")


def _lease(**changes: Any) -> dict[str, Any]:
    return {
        "lease_id": "action_approval:one",
        "tenant": "tenant-a",
        "kind": "action.approval",
        "status": "active",
        "revision": 3,
        "grant": {"kind": "restart", "target": "worker-a", "secret": "hidden"},
        **changes,
    }


def _caller(scopes: frozenset[str] | None = None) -> VerifiedCaller:
    granted = (
        frozenset({"approvals:read", "approvals:decide", "lease:write"})
        if scopes is None
        else scopes
    )
    return VerifiedCaller(
        principal="human:approver",
        tenant="tenant-a",
        effective_scopes=granted,
        engine_claims={
            "principal": "human:approver",
            "tenant": "tenant-a",
            "agent_id": "human:approver",
            "scopes": list(granted),
            "delegation": [],
            "policy_version": "policy-v1",
        },
        principal_kind="human",
        policy_revision="policy-v1",
    )


def _actor_ref() -> str:
    return "principal:sha256:" + hashlib.sha256(b"human:approver").hexdigest()


def _context(rows: list[dict[str, Any]], service: ApprovalService | None = None):
    leases = Leases(rows)
    context = SimpleNamespace(
        services={"approvals": service or ApprovalService()},
        client=SimpleNamespace(control_leases=leases),
        caller=_caller(),
        owner="human:approver",
        owner_ref=_actor_ref(),
        idempotency_key="approval-decision:one",
        service_identity=False,
    )
    return context, leases


def test_approval_contract_requires_exact_scope_and_console_confirmation() -> None:
    ops = {item.id: item for item in specs()}
    assert set(ops) == {
        "approvals.list",
        "approvals.get",
        "approvals.grant",
        "approvals.deny",
    }
    for name in ("list", "get"):
        op = ops[f"approvals.{name}"]
        assert op.scopes == {"approvals:read"}
        assert op.surfaces == {Surface.HTTP, Surface.CONSOLE}
    for name in ("grant", "deny"):
        op = ops[f"approvals.{name}"]
        assert op.scopes == {"approvals:decide"}
        assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
        assert op.confirm is Confirm.CONSOLE
        assert op.surfaces == {Surface.HTTP, Surface.CONSOLE}
    with pytest.raises(ValidationError):
        ApprovalDecisionParams(approval_id="action_approval:one", expected_revision=0)


def test_native_decision_binding_requires_generated_actor_time_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from epistemic_graph.generated import models

    service = ApprovalService(decision_authority=NativeLeaseDecisionAuthority())
    monkeypatch.setattr(
        models, "ControlLeaseView", SimpleNamespace(model_fields={"lease_id": object()})
    )
    assert service.serving_safe is False
    monkeypatch.setattr(
        models,
        "ControlLeaseView",
        SimpleNamespace(
            model_fields={
                "lease_id": object(),
                "transition_actor": object(),
                "transitioned_at_ms": object(),
            }
        ),
    )
    assert service.serving_safe is True


@pytest.mark.asyncio
async def test_read_uses_verified_tenant_and_projects_bounded_fields() -> None:
    context, leases = _context([_lease()])
    ops = {item.id: item for item in specs()}
    answer = await execute(context, {"limit": 10}, ops["approvals.list"])
    assert leases.calls == [
        (
            "list",
            {
                "tenant": "tenant-a",
                "kind": "action.approval",
                "status": "active",
                "limit": 10,
            },
        )
    ]
    pending = answer["value"]["pending"]
    assert pending == [
        {
            "approval_id": "action_approval:one",
            "status": "active",
            "revision": 3,
            "kind": "restart",
            "target": "worker-a",
            "expires_at_ms": None,
        }
    ]
    assert "secret" not in str(answer)
    found = await execute(
        context, {"approval_id": "action_approval:one"}, ops["approvals.get"]
    )
    assert found["value"] == pending[0]


@pytest.mark.asyncio
async def test_missing_or_cross_tenant_approval_fails_closed() -> None:
    op = next(item for item in specs() if item.id == "approvals.get")
    for rows in ([], [_lease(tenant="tenant-b")]):
        context, _ = _context(rows)
        with pytest.raises(OperationRefused) as refusal:
            await execute(context, {"approval_id": "action_approval:one"}, op)
        assert refusal.value.code == "POLICY_DENIED"


@pytest.mark.asyncio
async def test_decisions_require_bound_authority_and_active_revision() -> None:
    op = next(item for item in specs() if item.id == "approvals.grant")
    params = {"approval_id": "action_approval:one", "expected_revision": 3}
    context, leases = _context([_lease()])
    with pytest.raises(OperationRefused) as refusal:
        await execute(context, params, op)
    assert refusal.value.code == "UNAVAILABLE"
    assert leases.calls == []

    class Authority:
        async def decide(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "approval_id": kwargs["approval_id"],
                "decision": kwargs["decision"],
            }

    context, _ = _context(
        [_lease(status="consumed")], ApprovalService(decision_authority=Authority())
    )
    with pytest.raises(OperationRefused) as refusal:
        await execute(context, params, op)
    assert refusal.value.code == "PLAN_STALE"


@pytest.mark.asyncio
async def test_decision_authority_receives_verified_identity_and_revision() -> None:
    seen: list[dict[str, Any]] = []

    class Authority:
        async def decide(self, **kwargs: Any) -> dict[str, Any]:
            seen.append(kwargs)
            return {
                "approval_id": kwargs["approval_id"],
                "decision": kwargs["decision"],
            }

    context, _ = _context([_lease()], ApprovalService(decision_authority=Authority()))
    op = next(item for item in specs() if item.id == "approvals.deny")
    result = await execute(
        context,
        {"approval_id": "action_approval:one", "expected_revision": 3},
        op,
    )
    assert result["value"] == {
        "approval_id": "action_approval:one",
        "decision": "denied",
    }
    assert len(seen) == 1
    assert seen[0]["client"] is context.client
    assert {key: value for key, value in seen[0].items() if key != "client"} == {
        "tenant": "tenant-a",
        "approval_id": "action_approval:one",
        "expected_revision": 3,
        "decision": "denied",
        "approver": context.owner_ref,
        "idempotency_key": "approval-decision:one",
        "caller": context.caller,
    }


@pytest.mark.asyncio
async def test_native_lease_decision_checks_atomic_receipt_and_verified_actor() -> None:
    actor = _actor_ref()
    calls: list[dict[str, Any]] = []

    class NativeLeases:
        async def transition(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "outcome": "applied",
                "lease": {
                    "lease_id": "action_approval:one",
                    "kind": "action.approval",
                    "status": "consumed",
                    "revision": 4,
                    "transition_actor": actor,
                    "transitioned_at_ms": 500,
                    "expires_at_ms": 900,
                    "hard_expires_at_ms": 800,
                },
            }

    client = SimpleNamespace(control_leases=NativeLeases())
    authority = NativeLeaseDecisionAuthority()
    result = await authority.decide(
        client=client,
        tenant="tenant-a",
        approval_id="action_approval:one",
        expected_revision=3,
        decision="approved",
        approver=actor,
        idempotency_key="approval-decision:one",
        caller=_caller(),
    )
    assert result == {
        "approval_id": "action_approval:one",
        "decision": "approved",
        "revision": 4,
        "transitioned_at_ms": 500,
    }
    assert calls == [
        {
            "tenant": "tenant-a",
            "lease_id": "action_approval:one",
            "expected_revision": 3,
            "to": "consumed",
            "idempotency_key": "approval-decision:one",
        }
    ]


@pytest.mark.asyncio
async def test_native_lease_decision_rejects_expired_or_wrong_actor_receipt() -> None:
    actor = _actor_ref()

    class NativeLeases:
        async def transition(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "outcome": "applied",
                "lease": {
                    "lease_id": "action_approval:one",
                    "kind": "action.approval",
                    "status": "consumed",
                    "revision": 4,
                    "transition_actor": "principal:sha256:" + "b" * 64,
                    "transitioned_at_ms": 900,
                    "expires_at_ms": 900,
                    "hard_expires_at_ms": 800,
                },
            }

    authority = NativeLeaseDecisionAuthority()
    with pytest.raises(OperationRefused) as refusal:
        await authority.decide(
            client=SimpleNamespace(control_leases=NativeLeases()),
            tenant="tenant-a",
            approval_id="action_approval:one",
            expected_revision=3,
            decision="approved",
            approver=actor,
            idempotency_key="approval-decision:one",
            caller=_caller(),
        )
    assert refusal.value.code == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_scope_bridge_refuses_ungranted_lease_scope_before_native_write() -> None:
    leases = Leases([_lease()])
    client = SimpleNamespace(control_leases=leases)
    authority = NativeLeaseDecisionAuthority()
    args = {
        "client": client,
        "tenant": "tenant-a",
        "approval_id": "action_approval:one",
        "expected_revision": 3,
        "decision": "approved",
        "approver": _actor_ref(),
        "idempotency_key": "approval-decision:one",
    }
    with pytest.raises(OperationRefused) as refusal:
        await authority.decide(**args, caller=_caller(frozenset({"approvals:decide"})))
    assert refusal.value.code == "POLICY_DENIED"
    assert leases.calls == []

    caller = _caller()
    forged = replace(
        caller,
        engine_claims={**caller.engine_claims, "scopes": ["lease:write"]},
    )
    with pytest.raises(OperationRefused) as refusal:
        await authority.decide(**args, caller=forged)
    assert refusal.value.code == "UNAVAILABLE"
    assert leases.calls == []

    with pytest.raises(OperationRefused) as refusal:
        await authority.decide(
            **{**args, "approver": "principal:sha256:" + "b" * 64}, caller=caller
        )
    assert refusal.value.code == "UNAVAILABLE"
    assert leases.calls == []


@pytest.mark.asyncio
async def test_approval_decision_refuses_service_execution_before_lease_read() -> None:
    context, leases = _context(
        [_lease()], ApprovalService(decision_authority=NativeLeaseDecisionAuthority())
    )
    context.service_identity = True
    op = next(item for item in specs() if item.id == "approvals.grant")
    with pytest.raises(OperationRefused) as refusal:
        await execute(
            context,
            {"approval_id": "action_approval:one", "expected_revision": 3},
            op,
        )
    assert refusal.value.code == "UNAVAILABLE"
    assert leases.calls == []
