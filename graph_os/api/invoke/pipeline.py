"""Single operation chokepoint for MCP, HTTP, A2A, and console."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from epistemic_graph import EngineResponseError
from pydantic import BaseModel

from graph_os.api.generated.engine_errors import ENGINE_ERRORS
from graph_os.api.invoke.audit import audit_event
from graph_os.api.invoke.executor import (
    OperationRuntime,
    execution_context,
    prepare_executor,
)
from graph_os.api.invoke.plan import EgPlanStore, bind_plan
from graph_os.api.invoke.steps import (
    OpError,
    OpResult,
    SchemaValidate,
    VerifiedCaller,
    authenticate,
    principal_rule,
    require_scopes,
    validate_params,
)
from graph_os.api.registry import (
    AuditClass,
    Confirm,
    Effect,
    Executor,
    Idempotency,
    PrincipalRule,
    Surface,
)

DISPATCH_TIMEOUT_SECONDS = 320
MFA_FRESH_SECONDS = 900

PolicyCheck = Callable[[Any, VerifiedCaller], Awaitable[bool]]
AuditWrite = Callable[[Mapping[str, str], AuditClass, VerifiedCaller], Awaitable[None]]
AuditPreflight = Callable[
    [Mapping[str, str], AuditClass, VerifiedCaller], Awaitable[str]
]


def _service_fleet_caller(
    op: Any,
    caller: VerifiedCaller,
    *,
    idempotency_key: str | None,
    plan_ref: str | None,
) -> VerifiedCaller | OpError:
    """Use one audit/journal identity for retries of the same authorized call.

    EG binds payload and authority digests to this ID, rejecting a changed
    binding. Including them in the ID would permit a second reservation.
    """

    if plan_ref is not None:
        source = ("plan", plan_ref)
    elif idempotency_key is not None:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
        source = ("key", idempotency_key)
    elif op.effect == Effect.READ:
        source = ("request", caller.request_id or secrets.token_hex(16))
    else:
        return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    raw = json.dumps(
        [caller.tenant, caller.principal, op.id, *source],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return replace(caller, request_id=hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True, slots=True)
class FleetCallDecision:
    """Trusted live catalog authority for one exact child call."""

    effect: Effect
    confirm: Confirm
    principals: PrincipalRule
    executor: Executor
    required_scopes: frozenset[str]
    executor_scopes: frozenset[str]
    subject_id: str | None
    credential_mode: str


FleetEffect = Callable[
    [Any, Mapping[str, Any], VerifiedCaller],
    Awaitable[FleetCallDecision | tuple[Effect, Confirm, PrincipalRule]],
]


class OperationRefused(Exception):
    """A composite handler refusal retained by the shared invoke boundary."""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InvokeServices:
    registry: Any
    runtime: OperationRuntime
    plans: EgPlanStore
    policy_mode: str
    policy_check: PolicyCheck | None
    audit_write: AuditWrite
    fleet_effect: FleetEffect | None = None
    schema_validate: SchemaValidate | None = None
    audit_preflight: AuditPreflight | None = None


async def _policy(
    op: Any, caller: VerifiedCaller, services: InvokeServices
) -> OpError | None:
    if services.policy_mode == "off":
        return None
    if services.policy_mode != "on" or services.policy_check is None:
        return OpError("POLICY_UNAVAILABLE")
    try:
        allowed = await services.policy_check(op, caller)
    except Exception:
        return OpError("POLICY_UNAVAILABLE")
    return None if allowed else OpError("POLICY_DENIED")


async def _effect(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    surface: Surface,
    services: InvokeServices,
    *,
    plan_ref: str | None,
    resolved_from_intent: bool,
) -> OpError | OpResult | None:
    needs_plan = _requires_plan(op, resolved_from_intent)
    if not needs_plan:
        if plan_ref is not None:
            return OpError("PLAN_MISMATCH")
        return None
    binding = bind_plan(op, params, caller, services.registry.digest)
    if plan_ref is None:
        try:
            reference = await services.plans.issue(binding, params)
        except Exception:
            return OpError("UNAVAILABLE", {"reason": "plan lease unavailable"})
        details = {"plan_ref": reference, "op": op.id, "effect": op.effect.value}
        if op.confirm == Confirm.CONSOLE:
            details["console_url"] = f"/console/confirm/{reference}"
            return OpResult(code="STEP_UP_REQUIRED", details=details)
        return OpResult(code="CONFIRMATION_REQUIRED", details=details)
    if op.confirm == Confirm.CONSOLE:
        if not _console_ready(caller, surface):
            return OpError("PRINCIPAL_NOT_ALLOWED")
    try:
        return await services.plans.consume(plan_ref, binding)
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "plan lease unavailable"})


def _requires_plan(op: Any, resolved_from_intent: bool) -> bool:
    return op.confirm in {Confirm.PLAN, Confirm.CONSOLE} or (
        op.effect == Effect.WRITE and resolved_from_intent
    )


def _console_ready(caller: VerifiedCaller, surface: Surface) -> bool:
    if surface != Surface.CONSOLE or caller.delegated or caller.mfa_at_ms is None:
        return False
    import time

    age_ms = int(time.time() * 1000) - caller.mfa_at_ms
    return 0 <= age_ms <= MFA_FRESH_SECONDS * 1000


async def _effective_op(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
) -> tuple[Any, FleetCallDecision | None] | OpError:
    if op.id != "fleet.call":
        return op, None
    if services.fleet_effect is None:
        return OpError("UNAVAILABLE", {"reason": "fleet effect authority unavailable"})
    try:
        resolved = await services.fleet_effect(op, params, caller)
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "fleet effect authority unavailable"})
    if isinstance(resolved, FleetCallDecision):
        if not _valid_fleet_decision(op, resolved):
            return OpError("UNAVAILABLE", {"reason": "invalid fleet authority"})
        updated = op.model_copy(
            update={
                "effect": resolved.effect,
                "confirm": resolved.confirm,
                "principals": resolved.principals,
                "executor": resolved.executor,
                "scopes": op.scopes | resolved.required_scopes,
                "executor_scopes": resolved.executor_scopes,
            }
        )
        return updated, resolved
    if not isinstance(resolved, tuple) or len(resolved) != 3:
        return OpError("UNAVAILABLE", {"reason": "invalid fleet effect authority"})
    effect, confirm, principal = resolved
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (effect, Effect),
            (confirm, Confirm),
            (principal, PrincipalRule),
        )
    ):
        return OpError("UNAVAILABLE", {"reason": "invalid fleet effect authority"})
    return op.model_copy(
        update={"effect": effect, "confirm": confirm, "principals": principal}
    ), None


def _valid_fleet_decision(op: Any, decision: FleetCallDecision) -> bool:
    if not isinstance(decision.required_scopes, frozenset) or not isinstance(
        decision.executor_scopes, frozenset
    ):
        return False
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (decision.effect, Effect),
            (decision.confirm, Confirm),
            (decision.principals, PrincipalRule),
            (decision.executor, Executor),
        )
    ):
        return False
    if any(
        not scope or "*" in scope
        for scope in decision.required_scopes | decision.executor_scopes
    ):
        return False
    if decision.effect == Effect.ADMIN and decision.confirm != Confirm.CONSOLE:
        return False
    if (
        decision.effect == Effect.ADMIN
        and decision.principals != PrincipalRule.HUMAN_UNDELEGATED
    ):
        return False
    if decision.effect == Effect.DESTRUCTIVE and decision.confirm != Confirm.PLAN:
        return False
    if decision.executor == Executor.CALLER:
        return (
            decision.credential_mode == "delegated"
            and not decision.executor_scopes
            and decision.subject_id is None
        )
    return (
        decision.credential_mode == "service"
        and bool(decision.required_scopes)
        and bool(decision.executor_scopes)
        and isinstance(decision.subject_id, str)
        and 1 <= len(decision.subject_id) <= 256
        and op.audit != AuditClass.NONE
    )


async def _dispatch(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
    idempotency_key: str | None,
    fleet_decision: FleetCallDecision | None,
) -> Any:
    from agent_utilities.core.resource_priority import (
        PriorityClass,
        current_priority,
        priority_scope,
    )

    async def guarded() -> Any:
        if caller.session is None:
            async with execution_context(
                op,
                caller,
                services.runtime,
                idempotency_key,
                fleet_decision,
                services.registry.digest,
            ) as context:
                return await services.runtime.dispatch(op, params, context)
        from graph_os.mcp_server.bootstrap import authority_keepalive_scope

        async with authority_keepalive_scope(caller.session):
            async with execution_context(
                op,
                caller,
                services.runtime,
                idempotency_key,
                fleet_decision,
                services.registry.digest,
            ) as context:
                return await services.runtime.dispatch(op, params, context)

    if current_priority() is None:
        with priority_scope(PriorityClass.INTERACTIVE):
            return await guarded()
    return await guarded()


async def invoke(
    op_id: str,
    params: Mapping[str, Any],
    caller: VerifiedCaller | None,
    surface: Surface,
    *,
    services: InvokeServices,
    plan_ref: str | None = None,
    idempotency_key: str | None = None,
    resolved_from_intent: bool = False,
) -> OpResult | OpError:
    """Resolve, validate, authorize, confirm, execute, and audit exactly once."""

    op = services.registry.get(op_id)
    if op is None:
        return OpError("UNKNOWN_OP")
    if surface not in op.surfaces:
        return OpError("SURFACE_NOT_ALLOWED")
    validated = validate_params(op, params, services.schema_validate)
    if isinstance(validated, OpError):
        return validated
    refused = authenticate(caller)
    if refused is not None:
        return refused
    assert caller is not None
    for check in (principal_rule, require_scopes):
        refused = check(op, caller)
        if refused is not None:
            return refused
    refused = await _policy(op, caller, services)
    if refused is not None:
        return refused
    arguments = (
        validated.model_dump(mode="json")
        if isinstance(validated, BaseModel)
        else dict(validated)
    )
    effective = await _effective_op(op, arguments, caller, services)
    if isinstance(effective, OpError):
        return effective
    op, fleet_decision = effective
    refused = principal_rule(op, caller)
    if refused is not None:
        return refused
    refused = require_scopes(op, caller)
    if refused is not None:
        return refused
    subject_id = fleet_decision.subject_id if fleet_decision is not None else None
    refused = await prepare_executor(
        op, arguments, caller, services.runtime, verified_subject=subject_id
    )
    if refused is not None:
        return refused
    if not _requires_plan(op, resolved_from_intent):
        if plan_ref is not None:
            return OpError("PLAN_MISMATCH")
        if (
            op.effect == Effect.WRITE
            and op.idempotency != Idempotency.NATURAL
            and not idempotency_key
        ):
            return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    if plan_ref is not None and _requires_plan(op, resolved_from_intent):
        if op.confirm == Confirm.CONSOLE and not _console_ready(caller, surface):
            return OpError("PRINCIPAL_NOT_ALLOWED")
        try:
            _, refused = await services.plans.validate(
                plan_ref, bind_plan(op, arguments, caller, services.registry.digest)
            )
        except Exception:
            return OpError("UNAVAILABLE", {"reason": "plan lease unavailable"})
        if refused is not None:
            return refused
    audit_ref = ""
    will_execute = not _requires_plan(op, resolved_from_intent) or plan_ref is not None
    if (
        will_execute
        and fleet_decision is not None
        and fleet_decision.executor == Executor.SERVICE
    ):
        caller = _service_fleet_caller(
            op, caller, idempotency_key=idempotency_key, plan_ref=plan_ref
        )
        if isinstance(caller, OpError):
            return caller
    needs_audit = op.effect != Effect.READ or op.id == "fleet.call"
    if needs_audit and will_execute:
        if op.audit == AuditClass.NONE:
            return OpError("UNAVAILABLE", {"reason": "mutation audit class absent"})
        if services.audit_preflight is None:
            return OpError("UNAVAILABLE", {"reason": "audit preflight unavailable"})
        try:
            audit_ref = await services.audit_preflight(
                audit_event(op, arguments, caller, surface, "PENDING"), op.audit, caller
            )
        except Exception:
            return OpError("UNAVAILABLE", {"reason": "audit preflight unavailable"})
        if not isinstance(audit_ref, str) or not audit_ref:
            return OpError("UNAVAILABLE", {"reason": "audit preflight unavailable"})
    decision = await _effect(
        op,
        arguments,
        caller,
        surface,
        services,
        plan_ref=plan_ref,
        resolved_from_intent=resolved_from_intent,
    )
    if decision is not None:
        if audit_ref:
            try:
                await services.audit_write(
                    audit_event(
                        op, arguments, caller, surface, decision.code, audit_ref
                    ),
                    op.audit,
                    caller,
                )
            except Exception:
                return OpError("INDETERMINATE", {"reason": "audit outcome unavailable"})
        return decision
    status = "OK"
    try:
        value = await asyncio.wait_for(
            _dispatch(op, arguments, caller, services, idempotency_key, fleet_decision),
            timeout=DISPATCH_TIMEOUT_SECONDS,
        )
        result: OpResult | OpError = OpResult(value=value)
    except TimeoutError:
        status = "INDETERMINATE"
        result = OpError("TIMEOUT")
    except asyncio.CancelledError:
        status = "INDETERMINATE"
        if audit_ref:
            try:
                await services.audit_write(
                    audit_event(op, arguments, caller, surface, status, audit_ref),
                    op.audit,
                    caller,
                )
            except Exception:
                pass
        raise
    except OperationRefused as exc:
        status = exc.code
        result = OpError(exc.code, exc.details)
    except EngineResponseError as exc:
        # EG's wire code is typed; never infer authority from its private detail.
        if exc.code in ENGINE_ERRORS:
            status = exc.code
            result = OpError(exc.code, source="engine")
        else:
            status = "INTERNAL"
            result = OpError(status)
    except Exception:
        status = "INTERNAL"
        result = OpError(status)
    if audit_ref:
        try:
            await services.audit_write(
                audit_event(op, arguments, caller, surface, status, audit_ref),
                op.audit,
                caller,
            )
        except Exception:
            return OpError("INDETERMINATE", {"reason": "audit outcome unavailable"})
    return result
