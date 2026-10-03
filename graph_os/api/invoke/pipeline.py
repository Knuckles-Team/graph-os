"""Single operation chokepoint for MCP, HTTP, A2A, and console."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel

from graph_os.api.errors import EngineRefusal, FleetRefusal
from graph_os.api.invoke.audit import EffectJournal, EffectReservation, audit_event
from graph_os.api.invoke.executor import (
    OperationRuntime,
    execution_context,
    prepare_executor,
)
from graph_os.api.invoke.plan import PlanStore, bind_plan, params_digest
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
    Awaitable[FleetCallDecision],
]


class OperationRefused(Exception):
    """A composite handler refusal retained by the shared invoke boundary."""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InvokeServices:
    registry: Registry
    runtime: OperationRuntime
    plans: PlanStore
    policy_gate: PolicyGate
    audit_preflight: AuditPreflight
    audit_write: AuditWrite
    effects: EffectJournal | None = None
    native_idempotency: Mapping[str, str] = field(default_factory=dict)
    fleet_effect: FleetEffect | None = None
    schema_validate: SchemaValidate | None = None

    def __post_init__(self) -> None:
        ports = (
            (
                self.runtime,
                (
                    "verify_current",
                    "as_caller",
                    "as_service",
                    "check_subject_access",
                    "dispatch",
                ),
            ),
            (self.plans, ("issue", "validate", "consume")),
            (self.policy_gate, ("check_op",)),
        )
        if self.effects is not None and any(
            not callable(getattr(self.effects, name, None))
            for name in ("reserve", "complete")
        ):
            raise ValueError("effect reservation authority unavailable")
        for op_id, owner_contract in self.native_idempotency.items():
            if (
                self.registry.get(op_id) is None
                or not isinstance(owner_contract, str)
                or not owner_contract
            ):
                raise ValueError("invalid native idempotency owner coverage")
        from types import MappingProxyType

        object.__setattr__(
            self, "native_idempotency", MappingProxyType(dict(self.native_idempotency))
        )
        for owner, names in ports:
            if any(not callable(getattr(owner, name, None)) for name in names):
                raise ValueError("required invocation authority unavailable")
        if not callable(self.audit_preflight) or not callable(self.audit_write):
            raise ValueError("durable audit authority unavailable")
        if any(isinstance(op.params, EgSchemaRef) for op in self.registry):
            if not callable(self.schema_validate):
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
        if not isinstance(reference, str) or not reference:
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
        not isinstance(scope, str) or not scope or "*" in scope
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
    for check in (principal_rule, require_scopes):
        refused = check(op, caller)
        if refused is not None:
            return refused
    refused = await _policy(op, caller, services)
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


async def _record(
    op: Any,
    params: Mapping[str, Any],
    caller: VerifiedCaller,
    surface: Surface,
    services: InvokeServices,
    audit_ref: str,
    reservation: EffectReservation | None,
    result: OpResult | OpError,
) -> OpResult | OpError:
    try:
        if audit_ref:
            await services.audit_write(
                audit_event(op, params, caller, surface, result.code, audit_ref),
                op.audit,
                caller,
            )
        if reservation is not None and result.code not in {"INDETERMINATE", "TIMEOUT"}:
            assert services.effects is not None
            await services.effects.complete(reservation.reference, result)
    except Exception:
        return OpError("INDETERMINATE", {"audit_ref": audit_ref})
    return result


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
    if (op.effect == Effect.DESTRUCTIVE and op.confirm != Confirm.PLAN) or (
        op.effect == Effect.ADMIN
        and (
            op.confirm != Confirm.CONSOLE
            or op.principals != PrincipalRule.HUMAN_UNDELEGATED
        )
    ):
        return OpError("UNAVAILABLE", {"reason": "invalid effect authority"})
    if surface not in op.surfaces:
        return OpError("SURFACE_NOT_ALLOWED")
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
    refreshed = await _refresh(caller, services)
    if isinstance(refreshed, OpError):
        return refreshed
    caller = refreshed
    # Base scopes/principals apply even to fleet metadata resolution.
    for check in (principal_rule, require_scopes):
        refused = check(op, caller)
        if refused is not None:
            return refused
    refused = await _policy(op, caller, services)
    if refused is not None:
        return refused
    effective = await _effective_op(op, arguments, caller, services)
    if isinstance(effective, OpError):
        return effective
    op, decision = effective
    refused = await _authorize(op, arguments, caller, services, decision)
    if refused is not None:
        return refused
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256
    ):
        return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    needs_plan = _requires_plan(op, resolved_from_intent)
    if plan_ref is not None and (
        not isinstance(plan_ref, str) or not plan_ref or not needs_plan
    ):
        return OpError("PLAN_MISMATCH")
    if needs_plan and op.id in services.native_idempotency and services.effects is None:
        # OperationIdentity replays only by resubmitting the mutation. It cannot
        # prove whether a consumed confirmation already reached that owner. A
        # post-consumption retry must never become a new, unconfirmed effect.
        # Require an existing qualified replay/confirmation coordinator; never
        # create a GraphOS ledger or assume a consumed plan means completion.
        return OpError(
            "UNAVAILABLE", {"reason": "confirmed replay authority unavailable"}
        )
    if needs_plan and plan_ref is None:
        preview = await _effect(
            op,
            arguments,
            caller,
            surface,
            services,
            plan_ref=None,
            resolved_from_intent=resolved_from_intent,
        )
        assert preview is not None
        return preview
    if op.confirm == Confirm.CONSOLE and not _console_ready(caller, surface):
        return OpError("PRINCIPAL_NOT_ALLOWED")
    if (
        op.effect == Effect.WRITE
        and op.idempotency != Idempotency.NATURAL
        and not idempotency_key
        and not plan_ref
    ):
        return OpError("INVALID_ARGUMENT", {"field": "idempotency_key"})
    governed = op.effect != Effect.READ or op.id == "fleet.call"
    native_replay = op.id in services.native_idempotency and not needs_plan
    if governed and not native_replay and services.effects is None:
        return OpError("UNAVAILABLE", {"reason": "durable effect owner unavailable"})
    if governed:
        journal_key = idempotency_key
        if journal_key is None and op.idempotency == Idempotency.NATURAL:
            journal_key = "natural:" + params_digest(arguments)
        caller = _request_caller(
            op, caller, idempotency_key=journal_key, plan_ref=plan_ref
        )
        if isinstance(caller, OpError):
            return caller
    # Refresh after potentially blocking subject/PDP operations, before durable
    # reservation. Changed authority never silently consumes a prior plan.
    refreshed = await _refresh(caller, services)
    if isinstance(refreshed, OpError):
        return refreshed
    if refreshed != caller:
        return OpError("UNAUTHENTICATED")
    audit_ref = ""
    reservation = None
    if governed:
        if op.audit == AuditClass.NONE:
            return OpError("UNAVAILABLE", {"reason": "audit class unavailable"})
        try:
            audit_ref = await services.audit_preflight(
                audit_event(op, arguments, caller, surface, "PENDING"), op.audit, caller
            )
        except Exception:
            return OpError("UNAVAILABLE", {"reason": "audit reservation unavailable"})
        if not isinstance(audit_ref, str) or not audit_ref:
            return OpError("UNAVAILABLE", {"reason": "audit reservation unavailable"})
        if not native_replay:
            assert services.effects is not None
            try:
                reservation = await services.effects.reserve(
                    caller.request_id,
                    _journal_binding(op, arguments, caller, services, decision),
                )
            except Exception:
                return OpError(
                    "UNAVAILABLE", {"reason": "effect reservation unavailable"}
                )
            if (
                not isinstance(reservation, EffectReservation)
                or not reservation.reference
            ):
                return OpError(
                    "UNAVAILABLE", {"reason": "effect reservation unavailable"}
                )
            if reservation.state == "completed" and isinstance(
                reservation.outcome, (OpResult, OpError)
            ):
                refused = await _recheck(op, arguments, caller, services, decision)
                if refused is not None:
                    return refused
                return await _record(
                    op,
                    arguments,
                    caller,
                    surface,
                    services,
                    audit_ref,
                    None,
                    reservation.outcome,
                )
            if reservation.state != "acquired":
                return OpError(
                    "INDETERMINATE"
                    if reservation.state == "pending"
                    else "INVALID_ARGUMENT",
                    {"audit_ref": audit_ref},
                )
    effect = await _effect(
        op,
        arguments,
        caller,
        surface,
        services,
        plan_ref=plan_ref,
        resolved_from_intent=resolved_from_intent,
    )
    if effect is not None:
        return await _record(
            op, arguments, caller, surface, services, audit_ref, reservation, effect
        )
    try:
        value = await asyncio.wait_for(
            _dispatch(
                op,
                arguments,
                caller,
                services,
                ("plan:" + plan_ref) if plan_ref else idempotency_key,
                decision,
            ),
            timeout=DISPATCH_TIMEOUT_SECONDS,
        )
        result: OpResult | OpError = OpResult(value=value)
    except asyncio.CancelledError:
        await _record(
            op,
            arguments,
            caller,
            surface,
            services,
            audit_ref,
            reservation,
            OpError("INDETERMINATE", {"audit_ref": audit_ref}),
        )
        raise
    except OperationRefused as exc:
        result = OpError(exc.code, exc.details)
    except EngineRefusal as exc:
        # Error envelope validates against canonical generated evidence. No
        # handwritten code table or import-time generated metadata fallback.
        result = OpError(exc.code, source="engine")
    except FleetRefusal as exc:
        result = OpError(
            exc.code, {"server": exc.server, "tool": exc.tool}, source="fleet"
        )
    except TimeoutError:
        result = OpError(
            "INDETERMINATE" if governed else "TIMEOUT",
            {"audit_ref": audit_ref} if audit_ref else {},
        )
    except Exception:
        result = OpError(
            "INDETERMINATE" if governed else "INTERNAL",
            {"audit_ref": audit_ref} if audit_ref else {},
        )
    return await _record(
        op, arguments, caller, surface, services, audit_ref, reservation, result
    )
