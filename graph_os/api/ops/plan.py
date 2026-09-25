"""Attended plan review and exact server-side confirmation."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.invoke import OpError, invoke
from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
)


class PlanGetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_ref: str = Field(pattern=r"^graphos_plan:[0-9a-f]{48}$")


class PlanConfirmParams(PlanGetParams):
    idempotency_key: str = Field(min_length=1, max_length=128)


class PlanView(BaseModel):
    plan_ref: str
    op: str
    preview: dict[str, Any]


class PlanConfirmation(BaseModel):
    confirmed: bool
    result: Any


def _fresh_console(context: Any) -> None:
    caller = context.caller
    age = (
        int(time.time() * 1000) - caller.mfa_at_ms
        if caller.mfa_at_ms is not None
        else -1
    )
    if caller.principal_kind != "human" or caller.delegated or not 0 <= age <= 900_000:
        raise OperationRefused("PRINCIPAL_NOT_ALLOWED")


async def get_handler(
    context: Any, params: dict[str, Any], op: OpSpec
) -> dict[str, Any]:
    _fresh_console(context)
    services = context.services["invoke_services"]
    plan, refusal = await services.plans.get_console_plan(
        params["plan_ref"], context.caller, services.registry.digest
    )
    if refusal is not None:
        raise OperationRefused(refusal.code, refusal.details)
    assert plan is not None
    original = services.registry.get(plan["op"])
    if original is None or original.confirm != Confirm.CONSOLE:
        raise OperationRefused("PLAN_STALE")
    return {
        "plan_ref": plan["plan_ref"],
        "op": original.id,
        "preview": {"summary": original.summary, "params": plan["params"]},
    }


async def confirm_handler(
    context: Any, params: dict[str, Any], op: OpSpec
) -> dict[str, Any]:
    _fresh_console(context)
    services = context.services["invoke_services"]
    plan, refusal = await services.plans.get_console_plan(
        params["plan_ref"], context.caller, services.registry.digest
    )
    if refusal is not None:
        raise OperationRefused(refusal.code, refusal.details)
    assert plan is not None
    original = services.registry.get(plan["op"])
    if original is None or original.confirm != Confirm.CONSOLE:
        raise OperationRefused("PLAN_STALE")
    outcome = await invoke(
        original.id,
        plan["params"],
        context.caller,
        Surface.CONSOLE,
        services=services,
        plan_ref=plan["plan_ref"],
        idempotency_key=params["idempotency_key"],
    )
    if isinstance(outcome, OpError):
        raise OperationRefused(outcome.code, outcome.details)
    if outcome.code != "OK":
        raise OperationRefused(outcome.code, outcome.details)
    return {"confirmed": True, "result": outcome.value}


def specs() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="plan.get",
            verb=Verb.ASK,
            summary="Review an attended confirmation plan.",
            examples=("Show the pending confirmation plan",),
            params=PlanGetParams,
            result=PlanView,
            binding=Composite(handler="graph_os.api.ops.plan.get_handler"),
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            effect=Effect.READ,
            surfaces=frozenset({Surface.CONSOLE}),
        ),
        OpSpec(
            id="plan.confirm",
            verb=Verb.MANAGE,
            summary="Confirm the reviewed operation with fresh MFA.",
            examples=("Confirm this pending plan",),
            params=PlanConfirmParams,
            result=PlanConfirmation,
            binding=Composite(handler="graph_os.api.ops.plan.confirm_handler"),
            principals=PrincipalRule.HUMAN_UNDELEGATED,
            effect=Effect.ADMIN,
            confirm=Confirm.NONE,
            surfaces=frozenset({Surface.CONSOLE}),
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
    )
