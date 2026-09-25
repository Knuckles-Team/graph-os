"""Console plans persist only sealed replay arguments and bind the caller."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api.invoke.plan import EgPlanStore, PlanBinding, params_digest
from graph_os.api.invoke.steps import OpResult, VerifiedCaller
from graph_os.api.ops import plan as plan_ops
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    OpSpec,
    Registry,
    Surface,
    Verb,
)


class Leases:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def issue(self, **kwargs: Any) -> dict[str, str]:
        self.rows[kwargs["lease_id"]] = {
            **kwargs,
            "status": "active",
            "revision": 1,
        }
        return {"outcome": "issued"}

    async def get(self, *, tenant: str, lease_id: str) -> dict[str, Any] | None:
        return self.rows.get(lease_id)

    async def transition(self, **kwargs: Any) -> dict[str, str]:
        self.rows[kwargs["lease_id"]]["status"] = kwargs["to"]
        return {"outcome": "applied"}


class Client:
    def __init__(self) -> None:
        self.control_leases = Leases()


def caller(principal: str = "user:1", policy_revision: str = "rev:1") -> VerifiedCaller:
    return VerifiedCaller(
        principal=principal,
        tenant="t1",
        effective_scopes=frozenset({"ops:admin"}),
        engine_claims={"principal": principal, "tenant": "t1", "scopes": ["ops:admin"]},
        principal_kind="human",
        policy_revision=policy_revision,
        mfa_at_ms=int(time.time() * 1000),
    )


@pytest.mark.asyncio
async def test_console_plan_seals_replay_and_rejects_other_caller() -> None:
    client = Client()
    store = EgPlanStore(client, seal_key=b"s" * 32)
    params = {"target": "private-object"}
    binding = PlanBinding(
        "ops.delete",
        params_digest(params),
        "user:1",
        "t1",
        "rev:1",
        "registry:1",
        "admin",
        "console",
    )
    ref = await store.issue(binding, params)
    grant = client.control_leases.rows[ref]["grant"]
    assert "private-object" not in str(grant)
    assert "sealed_params" in grant
    plan, refusal = await store.get_console_plan(ref, caller(), "registry:1")
    assert refusal is None
    assert plan == {"plan_ref": ref, "op": "ops.delete", "params": params}
    for person, revision in (("user:2", "rev:1"), ("user:1", "rev:2")):
        plan, refusal = await store.get_console_plan(
            ref, caller(person, revision), "registry:1"
        )
        assert plan is None and refusal is not None
    grant["sealed_params"] = grant["sealed_params"][:-2] + "xx"
    plan, refusal = await store.get_console_plan(ref, caller(), "registry:1")
    assert plan is None and refusal is not None


@pytest.mark.asyncio
async def test_console_plan_requires_shared_seal_key_and_matching_digest() -> None:
    client = Client()
    params = {"target": "object"}
    binding = PlanBinding(
        "ops.delete",
        params_digest(params),
        "user:1",
        "t1",
        "rev:1",
        "registry:1",
        "admin",
        "console",
    )
    with pytest.raises(RuntimeError, match="seal key"):
        await EgPlanStore(client).issue(binding, params)
    with pytest.raises(ValueError, match="do not match"):
        await EgPlanStore(client, seal_key=b"s" * 32).issue(
            binding, {"target": "other"}
        )


@pytest.mark.asyncio
async def test_console_confirm_replays_only_server_sealed_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Input(BaseModel):
        target: str

    original = OpSpec(
        id="ops.delete",
        verb=Verb.MANAGE,
        summary="Delete one governed item",
        examples=("Delete this item",),
        params=Input,
        result=Input,
        binding=Composite(handler="graph_os.api.ops.plan.get_handler"),
        effect=Effect.ADMIN,
        confirm=Confirm.CONSOLE,
        audit=AuditClass.EVENT,
    )
    client = Client()
    plans = EgPlanStore(client, seal_key=b"s" * 32)
    params = {"target": "item:1"}
    registry = Registry([original])
    binding = PlanBinding(
        original.id,
        params_digest(params),
        "user:1",
        "t1",
        "rev:1",
        registry.digest,
        "admin",
        "console",
    )
    ref = await plans.issue(binding, params)
    services = SimpleNamespace(plans=plans, registry=registry)
    context = SimpleNamespace(caller=caller(), services={"invoke_services": services})
    viewed = await plan_ops.get_handler(context, {"plan_ref": ref}, plan_ops.specs()[0])
    assert viewed["preview"]["params"] == params
    calls: list[tuple[Any, ...]] = []

    async def invoke_original(
        op_id: str, arguments: Any, principal: Any, surface: Any, **kwargs: Any
    ) -> OpResult:
        calls.append((op_id, arguments, principal.principal, surface, kwargs))
        return OpResult(value={"deleted": True})

    monkeypatch.setattr(plan_ops, "invoke", invoke_original)
    confirmed = await plan_ops.confirm_handler(
        context, {"plan_ref": ref, "idempotency_key": "key:1"}, plan_ops.specs()[1]
    )
    assert confirmed == {"confirmed": True, "result": {"deleted": True}}
    assert calls == [
        (
            "ops.delete",
            params,
            "user:1",
            Surface.CONSOLE,
            {"services": services, "plan_ref": ref, "idempotency_key": "key:1"},
        )
    ]
