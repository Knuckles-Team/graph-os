"""Single operation chokepoint for MCP, HTTP, A2A, and console."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from graph_os.api.errors import EngineRefusal, FleetRefusal
from graph_os.api.invoke.audit import EffectJournal, EffectReservation, audit_event
from graph_os.api.invoke.executor import (
    OperationRuntime,
    execution_context,
    prepare_executor,
)
from graph_os.api.invoke.plan import PlanBinding, PlanStore, bind_plan, params_digest
from graph_os.api.invoke.steps import (
    OpError,
    OpResult,
    SchemaValidate,
    VerifiedCaller,
    authenticate,
    freeze_claims,
    principal_rule,
    require_scopes,
    validate_params,
)
from graph_os.api.policy.eunomia import PolicyGate
from graph_os.api.registry import (
    AuditClass,
    Confirm,
    Effect,
    EgSchemaRef,
    Executor,
    Idempotency,
    PrincipalRule,
    Registry,
    Surface,
)

DISPATCH_TIMEOUT_SECONDS = 320
MFA_FRESH_SECONDS = 900

AuditWrite = Callable[[Mapping[str, str], AuditClass, VerifiedCaller], Awaitable[None]]
AuditPreflight = Callable[
    [Mapping[str, str], AuditClass, VerifiedCaller], Awaitable[str]
]


def _request_caller(
    op: Any,
    caller: VerifiedCaller,
    *,
    idempotency_key: str | None,
    plan_ref: str | None,
) -> VerifiedCaller | OpError:
    """Use one audit/journal identity for retries of the same authorized call.

    The effect owner binds payload and authority digests to this ID, rejecting
    a changed binding. Including them in the ID would permit a second reservation.
    """

    if plan_ref is not None:
        source = ("plan", plan_ref)
    elif idempotency_key is not None:
        if not _valid_key(idempotency_key):
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
    Awaitable[FleetCallDecision],
]


class OperationRefused(Exception):
    """A composite handler refusal retained by the shared invoke boundary."""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(code)


_RUNTIME_PORTS = (
    "verify_current",
    "as_caller",
    "as_service",
    "check_subject_access",
    "dispatch",
)
_PLAN_PORTS = ("issue", "validate", "consume")


def _has_methods(owner: Any, names: tuple[str, ...]) -> bool:
    return all(callable(getattr(owner, name, None)) for name in names)


def _valid_key(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256


@dataclass(frozen=True, slots=True)
class InvokeServices:
    registry: Registry
    runtime: OperationRuntime
    plans: PlanStore
    policy_gate: PolicyGate
    audit_preflight: AuditPreflight
    audit_write: AuditWrite
    effects: EffectJournal | None = None
    external_effect_operations: frozenset[str] = frozenset()
    native_idempotency: Mapping[str, str] = field(default_factory=dict)
    fleet_effect: FleetEffect | None = None
    schema_validate: SchemaValidate | None = None

    def __post_init__(self) -> None:
        self._check_effect_coverage()
        self._check_native_idempotency()
        object.__setattr__(
            self, "native_idempotency", MappingProxyType(dict(self.native_idempotency))
        )
        self._check_authorities()

    def _check_effect_coverage(self) -> None:
        if self.effects is not None and not _has_methods(
            self.effects, ("reserve", "complete")
        ):
            raise ValueError("effect reservation authority unavailable")
        if not isinstance(self.external_effect_operations, frozenset) or any(
            not isinstance(op_id, str) or self.registry.get(op_id) is None
            for op_id in self.external_effect_operations
        ):
            raise ValueError("external effect coverage requires exact operations")
        if self.external_effect_operations and self.effects is None:
            raise ValueError("external effect coverage has no owner authority")

    def _check_native_idempotency(self) -> None:
        if any(
            self.registry.get(op_id) is None
            or not isinstance(owner_contract, str)
            or not owner_contract
            for op_id, owner_contract in self.native_idempotency.items()
        ):
            raise ValueError("invalid native idempotency owner coverage")

    def _check_authorities(self) -> None:
        ports = (
            (self.runtime, _RUNTIME_PORTS),
            (self.plans, _PLAN_PORTS),
            (self.policy_gate, ("check_op",)),
        )
        if not all(_has_methods(owner, names) for owner, names in ports):
            raise ValueError("required invocation authority unavailable")
        if not callable(self.audit_preflight) or not callable(self.audit_write):
            raise ValueError("durable audit authority unavailable")
        needs_schema = any(isinstance(op.params, EgSchemaRef) for op in self.registry)
        if needs_schema and not callable(self.schema_validate):
            raise ValueError("provider schema authority unavailable")
        if self.registry.get("fleet.call") is not None and not callable(
            self.fleet_effect
        ):
            raise ValueError("fleet effect authority unavailable")


async def _policy(
    op: Any, caller: VerifiedCaller, services: InvokeServices
) -> OpError | None:
    try:
        allowed = await services.policy_gate.check_op(op, caller)
    except Exception:
        return OpError("POLICY_UNAVAILABLE")
    return None if allowed is True else OpError("POLICY_DENIED")


async def _base_authority(
    op: Any, caller: VerifiedCaller, services: InvokeServices
) -> OpError | None:
    for check in (principal_rule, require_scopes):
        refused = check(op, caller)
        if refused is not None:
            return refused
    return await _policy(op, caller, services)


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
    if not _requires_plan(op, resolved_from_intent):
        return OpError("PLAN_MISMATCH") if plan_ref is not None else None
    binding = bind_plan(op, params, caller, services.registry.digest)
    if plan_ref is None:
        return await _issue_plan(op, binding, params, services)
    if op.confirm == Confirm.CONSOLE and not _console_ready(caller, surface):
        return OpError("PRINCIPAL_NOT_ALLOWED")
    try:
        return await services.plans.consume(plan_ref, binding)
    except Exception:
        return OpError("UNAVAILABLE", {"reason": "plan lease unavailable"})


async def _issue_plan(
    op: Any,
    binding: PlanBinding,
    params: Mapping[str, Any],
    services: InvokeServices,
) -> OpResult | OpError:
    unavailable = OpError("UNAVAILABLE", {"reason": "plan lease unavailable"})
    try:
        reference = await services.plans.issue(binding, params)
    except Exception:
        return unavailable
    if not isinstance(reference, str) or not reference:
        return unavailable
    details = {"plan_ref": reference, "op": op.id, "effect": op.effect.value}
    if op.confirm == Confirm.CONSOLE:
        details["console_url"] = f"/console/confirm/{reference}"
        return OpResult(code="STEP_UP_REQUIRED", details=details)
    return OpResult(code="CONFIRMATION_REQUIRED", details=details)


def _requires_plan(op: Any, resolved_from_intent: bool) -> bool:
    return op.confirm in {Confirm.PLAN, Confirm.CONSOLE} or (
        op.effect == Effect.WRITE and resolved_from_intent
    )


def _console_ready(caller: VerifiedCaller, surface: Surface) -> bool:
    if (
        surface != Surface.CONSOLE
        or caller.delegated
        or caller.mfa_at_ms is None
        or caller.principal_kind != "human"
        or caller.credential_kind != "session"
    ):
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
    return OpError("UNAVAILABLE", {"reason": "invalid fleet effect authority"})


def _effect_authority_valid(
    effect: Effect, confirm: Confirm, principals: PrincipalRule
) -> bool:
    """Destructive effects need a plan; admin effects need an undelegated human."""
    if effect == Effect.DESTRUCTIVE:
        return confirm == Confirm.PLAN
    if effect == Effect.ADMIN:
        return (
            confirm == Confirm.CONSOLE and principals == PrincipalRule.HUMAN_UNDELEGATED
        )
    return True


def _fleet_types_valid(decision: FleetCallDecision) -> bool:
    if not isinstance(decision.required_scopes, frozenset) or not isinstance(
        decision.executor_scopes, frozenset
    ):
        return False
    typed = all(
        isinstance(value, expected)
        for value, expected in (
            (decision.effect, Effect),
            (decision.confirm, Confirm),
            (decision.principals, PrincipalRule),
            (decision.executor, Executor),
        )
    )
    return typed and all(
        isinstance(scope, str) and bool(scope) and "*" not in scope
        for scope in decision.required_scopes | decision.executor_scopes
    )


def _fleet_executor_valid(op: Any, decision: FleetCallDecision) -> bool:
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


def _valid_fleet_decision(op: Any, decision: FleetCallDecision) -> bool:
    return (
        _fleet_types_valid(decision)
        and _effect_authority_valid(
            decision.effect, decision.confirm, decision.principals
        )
        and _fleet_executor_valid(op, decision)
    )


async def _refresh(
    caller: VerifiedCaller | None, services: InvokeServices
) -> VerifiedCaller | OpError:
    refused = authenticate(caller)
    if refused is not None:
        return refused
    assert caller is not None
    try:
        current = await services.runtime.verify_current(caller)
    except Exception:
        return OpError("UNAUTHENTICATED")
    refused = authenticate(current)
    if refused is not None:
        return refused
    if (current.principal, current.tenant, current.principal_kind) != (
        caller.principal,
        caller.tenant,
        caller.principal_kind,
    ):
        return OpError("UNAUTHENTICATED")
    current = replace(current, request_id=caller.request_id)
    if current != caller:
        return OpError("UNAUTHENTICATED")
    return current


async def _authorize(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
    decision: FleetCallDecision | None,
) -> OpError | None:
    refreshed = await _refresh(caller, services)
    if isinstance(refreshed, OpError):
        return refreshed
    refused = await _base_authority(op, caller, services)
    if refused is not None:
        return refused
    return await prepare_executor(
        op,
        params,
        caller,
        services.runtime,
        verified_subject=decision.subject_id if decision else None,
    )


async def _recheck(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
    decision: FleetCallDecision | None,
) -> OpError | None:
    """Invalidate checks if awaited authority/client/metadata work changed facts."""
    refused = await _authorize(op, params, caller, services, decision)
    if refused is not None:
        return refused
    if decision is not None:
        current = await _effective_op(
            services.registry.get(op.id), params, caller, services
        )
        if isinstance(current, OpError):
            return current
        if current[1] != decision:
            return OpError("POLICY_DENIED")
    refreshed = await _refresh(caller, services)
    return refreshed if isinstance(refreshed, OpError) else None


async def _dispatch(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
    idempotency_key: str | None,
    decision: FleetCallDecision | None,
) -> Any:
    async with execution_context(
        op,
        caller,
        services.runtime,
        idempotency_key,
        decision,
        services.registry.digest,
    ) as context:
        refused = await _recheck(op, params, caller, services, decision)
        if refused is not None:
            raise OperationRefused(refused.code, refused.details)
        return await services.runtime.dispatch(op, params, context)


def _journal_binding(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    services: InvokeServices,
    decision: FleetCallDecision | None,
) -> str:
    binding = bind_plan(op, params, caller, services.registry.digest).as_grant()
    binding["authority"] = params_digest(
        {
            "scopes": sorted(caller.effective_scopes),
            "executor_scopes": sorted(op.executor_scopes),
            "principal_kind": caller.principal_kind,
            "delegated": caller.delegated,
            "executor": op.executor.value,
            "subject": decision.subject_id if decision else None,
        }
    )
    return params_digest(binding)


@dataclass(frozen=True, slots=True)
class _Call:
    """One authorized request after validation and effective-operation resolution."""

    op: Any
    arguments: Mapping[str, Any]
    caller: VerifiedCaller
    surface: Surface
    services: InvokeServices
    decision: FleetCallDecision | None
    plan_ref: str | None
    idempotency_key: str | None
    resolved_from_intent: bool

    @property
    def needs_plan(self) -> bool:
        return _requires_plan(self.op, self.resolved_from_intent)

    @property
    def external_replay(self) -> bool:
        return self.op.id in self.services.external_effect_operations

    @property
    def native_replay(self) -> bool:
        return self.op.id in self.services.native_idempotency and not self.needs_plan

    @property
    def governed(self) -> bool:
        return self.op.effect != Effect.READ or self.op.id == "fleet.call"

    async def effect(self, plan_ref: str | None) -> OpError | OpResult | None:
        return await _effect(
            self.op,
            self.arguments,
            self.caller,
            self.surface,
            self.services,
            plan_ref=plan_ref,
            resolved_from_intent=self.resolved_from_intent,
        )


async def _record(
    call: _Call,
    audit_ref: str,
    reservation: EffectReservation | None,
    result: OpResult | OpError,
) -> OpResult | OpError:
    services = call.services
    try:
        if audit_ref:
            await services.audit_write(
                audit_event(
                    call.op,
                    call.arguments,
                    call.caller,
                    call.surface,
                    result.code,
                    audit_ref,
                ),
                call.op.audit,
                call.caller,
            )
        if reservation is not None and result.code not in {"INDETERMINATE", "TIMEOUT"}:
            assert services.effects is not None
            await services.effects.complete(reservation.reference, result)
    except Exception:
        return OpError("INDETERMINATE", {"audit_ref": audit_ref})
    return result


def _arguments(
    op: Any, params: Mapping[str, Any], services: InvokeServices
) -> Mapping[str, Any] | OpError:
    validated = validate_params(op, params, services.schema_validate)
    if isinstance(validated, OpError):
        return validated
    try:
        arguments = freeze_claims(
            validated.model_dump(mode="json")
            if isinstance(validated, BaseModel)
            else validated
        )
        params_digest(arguments)
    except (TypeError, ValueError):
        return OpError("INVALID_ARGUMENT")
    return arguments


async def _resolve(
    op: Any,
    arguments: Mapping[str, Any],
    caller: VerifiedCaller | None,
    services: InvokeServices,
) -> tuple[Any, VerifiedCaller, FleetCallDecision | None] | OpError:
    refreshed = await _refresh(caller, services)
    if isinstance(refreshed, OpError):
        return refreshed
    # Base scopes/principals apply even to fleet metadata resolution.
    refused = await _base_authority(op, refreshed, services)
    if refused is not None:
        return refused
    effective = await _effective_op(op, arguments, refreshed, services)
    if isinstance(effective, OpError):
        return effective
    effective_op, decision = effective
    refused = await _authorize(effective_op, arguments, refreshed, services, decision)
    if refused is not None:
        return refused
    return effective_op, refreshed, decision


def _request_refusal(call: _Call) -> OpError | None:
    if call.idempotency_key is not None and not _valid_key(call.idempotency_key):
        return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    plan_ref = call.plan_ref
    if plan_ref is not None and (
        not isinstance(plan_ref, str) or not plan_ref or not call.needs_plan
    ):
        return OpError("PLAN_MISMATCH")
    if call.needs_plan and not call.external_replay:
        # OperationIdentity replays only by resubmitting the mutation. It cannot
        # prove whether a consumed confirmation already reached that owner. A
        # post-consumption retry must never become a new, unconfirmed effect.
        # Require an existing qualified replay/confirmation coordinator; never
        # create a GraphOS ledger or assume a consumed plan means completion.
        return OpError(
            "UNAVAILABLE", {"reason": "confirmed replay authority unavailable"}
        )
    return None


def _missing_idempotency_key(call: _Call) -> bool:
    op = call.op
    return (
        op.effect == Effect.WRITE
        and op.idempotency != Idempotency.NATURAL
        and not call.idempotency_key
        and not call.plan_ref
    )


def _execution_refusal(call: _Call) -> OpError | None:
    if call.op.confirm == Confirm.CONSOLE and not _console_ready(
        call.caller, call.surface
    ):
        return OpError("PRINCIPAL_NOT_ALLOWED")
    if _missing_idempotency_key(call):
        return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    if call.governed and not call.native_replay and not call.external_replay:
        return OpError("UNAVAILABLE", {"reason": "durable effect owner unavailable"})
    return None


async def _confirmation_gate(call: _Call) -> OpError | OpResult | None:
    """Refuse malformed requests, or answer an unconfirmed plan with a preview."""
    refused = _request_refusal(call)
    if refused is not None:
        return refused
    if call.needs_plan and call.plan_ref is None:
        preview = await call.effect(None)
        assert preview is not None
        return preview
    return _execution_refusal(call)


def _journal_key(call: _Call) -> str | None:
    if call.idempotency_key is None and call.op.idempotency == Idempotency.NATURAL:
        return "natural:" + params_digest(call.arguments)
    return call.idempotency_key


async def _bind_caller(call: _Call) -> VerifiedCaller | OpError:
    caller: VerifiedCaller | OpError = call.caller
    if call.governed:
        caller = _request_caller(
            call.op,
            call.caller,
            idempotency_key=_journal_key(call),
            plan_ref=call.plan_ref,
        )
        if isinstance(caller, OpError):
            return caller
    # Refresh after potentially blocking subject/PDP operations, before durable
    # reservation. Changed authority never silently consumes a prior plan.
    refreshed = await _refresh(caller, call.services)
    if isinstance(refreshed, OpError):
        return refreshed
    return caller if refreshed == caller else OpError("UNAUTHENTICATED")


async def _audit_preflight(call: _Call) -> str | OpError:
    op = call.op
    if op.audit == AuditClass.NONE:
        return OpError("UNAVAILABLE", {"reason": "audit class unavailable"})
    unavailable = OpError("UNAVAILABLE", {"reason": "audit reservation unavailable"})
    try:
        audit_ref = await call.services.audit_preflight(
            audit_event(op, call.arguments, call.caller, call.surface, "PENDING"),
            op.audit,
            call.caller,
        )
    except Exception:
        return unavailable
    return audit_ref if isinstance(audit_ref, str) and audit_ref else unavailable


async def _reserve_effect(call: _Call) -> EffectReservation | OpError:
    services = call.services
    assert services.effects is not None
    unavailable = OpError("UNAVAILABLE", {"reason": "effect reservation unavailable"})
    try:
        reservation = await services.effects.reserve(
            call.caller.request_id,
            _journal_binding(
                call.op, call.arguments, call.caller, services, call.decision
            ),
        )
    except Exception:
        return unavailable
    if not isinstance(reservation, EffectReservation) or not reservation.reference:
        return unavailable
    return reservation


async def _replay_completed(
    call: _Call, audit_ref: str, outcome: OpResult | OpError
) -> OpResult | OpError:
    refused = await _recheck(
        call.op, call.arguments, call.caller, call.services, call.decision
    )
    if refused is not None:
        return refused
    return await _record(call, audit_ref, None, outcome)


Reserved = tuple[str, EffectReservation | None]


async def _reserve(call: _Call) -> Reserved | OpResult | OpError:
    """Reserve durable audit and effect ownership, or replay a completed effect."""
    if not call.governed:
        return "", None
    audit_ref = await _audit_preflight(call)
    if isinstance(audit_ref, OpError):
        return audit_ref
    if call.native_replay:
        return audit_ref, None
    reservation = await _reserve_effect(call)
    if isinstance(reservation, OpError):
        return reservation
    if reservation.state == "completed" and isinstance(
        reservation.outcome, (OpResult, OpError)
    ):
        return await _replay_completed(call, audit_ref, reservation.outcome)
    if reservation.state != "acquired":
        code = "INDETERMINATE" if reservation.state == "pending" else "INVALID_ARGUMENT"
        return OpError(code, {"audit_ref": audit_ref})
    return audit_ref, reservation


def _unsettled(call: _Call, audit_ref: str, ungoverned_code: str) -> OpError:
    return OpError(
        "INDETERMINATE" if call.governed else ungoverned_code,
        {"audit_ref": audit_ref} if audit_ref else {},
    )


async def _dispatch_outcome(call: _Call, audit_ref: str) -> OpResult | OpError:
    dispatch_key = ("plan:" + call.plan_ref) if call.plan_ref else call.idempotency_key
    try:
        value = await asyncio.wait_for(
            _dispatch(
                call.op,
                call.arguments,
                call.caller,
                call.services,
                dispatch_key,
                call.decision,
            ),
            timeout=DISPATCH_TIMEOUT_SECONDS,
        )
    except OperationRefused as exc:
        return OpError(exc.code, exc.details)
    except EngineRefusal as exc:
        # Error envelope validates against canonical generated evidence. No
        # handwritten code table or import-time generated metadata fallback.
        return OpError(exc.code, source="engine")
    except FleetRefusal as exc:
        return OpError(
            exc.code, {"server": exc.server, "tool": exc.tool}, source="fleet"
        )
    except TimeoutError:
        return _unsettled(call, audit_ref, "TIMEOUT")
    except Exception:
        return _unsettled(call, audit_ref, "INTERNAL")
    return OpResult(value=value)


async def _execute(
    call: _Call, audit_ref: str, reservation: EffectReservation | None
) -> OpResult | OpError:
    effect = await call.effect(call.plan_ref)
    if effect is not None:
        return await _record(call, audit_ref, reservation, effect)
    try:
        result = await _dispatch_outcome(call, audit_ref)
    except asyncio.CancelledError:
        indeterminate = OpError("INDETERMINATE", {"audit_ref": audit_ref})
        await _record(call, audit_ref, reservation, indeterminate)
        raise
    return await _record(call, audit_ref, reservation, result)


def _static_refusal(op: Any, surface: Surface) -> OpError | None:
    if not _effect_authority_valid(op.effect, op.confirm, op.principals):
        return OpError("UNAVAILABLE", {"reason": "invalid effect authority"})
    if surface not in op.surfaces:
        return OpError("SURFACE_NOT_ALLOWED")
    return None


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
    """One fail-closed validation, authorization, effect and outcome boundary."""
    op = services.registry.get(op_id)
    if op is None:
        return OpError("UNKNOWN_OP")
    refused = _static_refusal(op, surface)
    if refused is not None:
        return refused
    arguments = _arguments(op, params, services)
    if isinstance(arguments, OpError):
        return arguments
    resolved = await _resolve(op, arguments, caller, services)
    if isinstance(resolved, OpError):
        return resolved
    effective_op, verified, decision = resolved
    call = _Call(
        effective_op,
        arguments,
        verified,
        surface,
        services,
        decision,
        plan_ref,
        idempotency_key,
        resolved_from_intent,
    )
    early = await _confirmation_gate(call)
    if early is not None:
        return early
    bound = await _bind_caller(call)
    if isinstance(bound, OpError):
        return bound
    call = replace(call, caller=bound)
    reserved = await _reserve(call)
    if not isinstance(reserved, tuple):
        return reserved
    return await _execute(call, *reserved)
