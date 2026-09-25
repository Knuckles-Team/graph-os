"""Focused authority and confirmation checks for the shared chokepoint."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.invoke import InvokeServices, OpError, VerifiedCaller, invoke
from graph_os.api.invoke.executor import BoundOperationRuntime
from graph_os.api.invoke.pipeline import FleetCallDecision
from graph_os.api.invoke.plan import EgPlanStore
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Confirm,
    Effect,
    Executor,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Registry,
    SubjectRef,
    SubjectSource,
    Surface,
    Verb,
)


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str


class TenantParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FleetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    ok: bool


class FakeLeases:
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
        row = self.rows.get(lease_id)
        return row if row and row["tenant"] == tenant else None

    async def transition(self, **kwargs: Any) -> dict[str, str]:
        row = self.rows[kwargs["lease_id"]]
        if row["revision"] != kwargs["expected_revision"] or row["status"] != "active":
            return {"outcome": "conflict"}
        row["status"] = kwargs["to"]
        row["revision"] += 1
        return {"outcome": "applied"}


class FakeRuntime:
    service_scopes = frozenset({"node:write"})

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.checked_subjects: list[str] = []
        self.audit_preflights: list[dict[str, str]] = []
        self.readable = True

    @asynccontextmanager
    async def as_caller(self, caller: VerifiedCaller):
        yield {"identity": caller.principal}

    @asynccontextmanager
    async def as_service(self, tenant: str):
        yield {"identity": "svc:graph-os", "tenant": tenant}

    async def check_subject_access(self, caller: VerifiedCaller, subject: str) -> bool:
        self.checked_subjects.append(subject)
        return self.readable and caller.tenant == "t1" and subject == "item:1"

    async def dispatch(
        self, op: OpSpec, params: dict[str, Any], context: Any
    ) -> dict[str, bool]:
        self.calls.append((context.client["identity"], context.owner))
        return {"ok": True}


def caller(
    principal: str = "user:1",
    *,
    scopes: frozenset[str] = frozenset({"item:write"}),
    mfa_at_ms: int | None = None,
) -> VerifiedCaller:
    return VerifiedCaller(
        principal=principal,
        tenant="t1",
        effective_scopes=scopes,
        engine_claims={
            "principal": principal,
            "tenant": "t1",
            "scopes": sorted(scopes),
        },
        principal_kind="human",
        policy_revision="p1",
        request_id="req-1",
        mfa_at_ms=mfa_at_ms,
    )


def op(**changes: Any) -> OpSpec:
    values = dict(
        id="items.change",
        verb=Verb.WRITE,
        summary="Change item",
        examples=("change item",),
        params=Params,
        result=Result,
        binding=Composite(handler="graph_os.items.change"),
        effect=Effect.DESTRUCTIVE,
        principals=PrincipalRule.HUMAN,
        scopes=frozenset({"item:write"}),
        idempotency=Idempotency.KEY_REQUIRED,
        audit=AuditClass.EVENT,
        surfaces=frozenset({Surface.MCP, Surface.HTTP, Surface.A2A, Surface.CONSOLE}),
    )
    values.update(changes)
    return OpSpec(**values)


def services(
    spec: OpSpec, runtime: FakeRuntime | None = None
) -> tuple[InvokeServices, FakeRuntime, list[dict[str, str]]]:
    engine = runtime or FakeRuntime()
    client = type("Client", (), {"control_leases": FakeLeases()})()
    events: list[dict[str, str]] = []

    async def audit(event: dict[str, str], audit_class: AuditClass) -> None:
        events.append(event)

    async def preflight(event: dict[str, str], audit_class: AuditClass) -> str:
        engine.audit_preflights.append(event)
        return "audit:1"

    return (
        InvokeServices(
            Registry([spec]),
            engine,
            EgPlanStore(client, seal_key=b"s" * 32),
            "off",
            None,
            audit,
            audit_preflight=preflight,
        ),
        engine,
        events,
    )


@pytest.mark.asyncio
async def test_preview_resubmit_consumes_once_and_audits_digest_only() -> None:
    app, engine, events = services(op())
    args = {"subject": "item:1"}
    preview = await invoke("items.change", args, caller(), Surface.MCP, services=app)
    assert preview.code == "CONFIRMATION_REQUIRED"
    assert engine.calls == []
    ref = preview.details["plan_ref"]
    result = await invoke(
        "items.change", args, caller(), Surface.HTTP, services=app, plan_ref=ref
    )
    assert hasattr(result, "value"), result
    assert result.value == {"ok": True}
    assert engine.calls == [("user:1", "user:1")]
    replay = await invoke(
        "items.change", args, caller(), Surface.A2A, services=app, plan_ref=ref
    )
    assert replay == OpError("PLAN_EXPIRED")
    assert len(events) == 1 and "item:1" not in str(events)
    assert engine.audit_preflights[0]["result_status"] == "PENDING"
    assert events[0]["audit_ref"] == "audit:1"


@pytest.mark.asyncio
async def test_plan_is_bound_to_caller_params_policy_and_registry() -> None:
    app, engine, _ = services(op())
    preview = await invoke(
        "items.change", {"subject": "item:1"}, caller(), Surface.MCP, services=app
    )
    ref = preview.details["plan_ref"]
    wrong_actor = await invoke(
        "items.change",
        {"subject": "item:1"},
        caller("user:2"),
        Surface.MCP,
        services=app,
        plan_ref=ref,
    )
    wrong_params = await invoke(
        "items.change",
        {"subject": "item:2"},
        caller(),
        Surface.MCP,
        services=app,
        plan_ref=ref,
    )
    assert wrong_actor == OpError("PLAN_MISMATCH")
    assert wrong_params == OpError("PLAN_MISMATCH")
    assert engine.calls == []


@pytest.mark.asyncio
async def test_scope_and_nested_authority_refused_before_dispatch() -> None:
    app, engine, _ = services(op())
    missing = await invoke(
        "items.change",
        {"subject": "item:1"},
        caller(scopes=frozenset()),
        Surface.MCP,
        services=app,
    )
    forged = await invoke(
        "items.change",
        {"subject": "item:1", "_actor": "root"},
        caller(),
        Surface.MCP,
        services=app,
    )
    assert missing == OpError("SCOPE_REQUIRED", {"missing_scopes": ["item:write"]})
    assert forged.code == "INVALID_ARGUMENT"
    assert engine.calls == []


@pytest.mark.asyncio
async def test_service_executor_requires_caller_read_before_service_identity() -> None:
    service_op = op(
        executor=Executor.SERVICE,
        executor_scopes=frozenset({"node:write"}),
        subject=SubjectRef(path="subject"),
        effect=Effect.READ,
        audit=AuditClass.NONE,
    )
    app, engine, _ = services(service_op)
    engine.readable = False
    denied = await invoke(
        "items.change", {"subject": "item:1"}, caller(), Surface.MCP, services=app
    )
    assert denied == OpError("SUBJECT_ACCESS_DENIED")
    engine.readable = True
    allowed = await invoke(
        "items.change", {"subject": "item:1"}, caller(), Surface.MCP, services=app
    )
    assert allowed.value == {"ok": True}
    assert engine.calls == [("svc:graph-os", "user:1")]


@pytest.mark.asyncio
async def test_tenant_subject_comes_from_verified_caller_only() -> None:
    tenant_op = op(
        params=TenantParams,
        executor=Executor.SERVICE,
        executor_scopes=frozenset({"node:write"}),
        subject=SubjectRef(source=SubjectSource.CALLER_TENANT),
        effect=Effect.READ,
        audit=AuditClass.NONE,
    )
    app, engine, _ = services(tenant_op)

    async def tenant_read(caller: VerifiedCaller, subject: str) -> bool:
        engine.checked_subjects.append(subject)
        return subject == caller.tenant

    engine.check_subject_access = tenant_read
    allowed = await invoke("items.change", {}, caller(), Surface.HTTP, services=app)
    assert allowed.value == {"ok": True}
    assert engine.checked_subjects == ["t1"]
    forged = await invoke(
        "items.change", {"subject": "item:1"}, caller(), Surface.HTTP, services=app
    )
    assert forged.code == "INVALID_ARGUMENT"
    assert engine.checked_subjects == ["t1"]


@pytest.mark.asyncio
async def test_admin_requires_console_and_fresh_mfa() -> None:
    import time

    app, engine, _ = services(
        op(effect=Effect.ADMIN, principals=PrincipalRule.HUMAN_UNDELEGATED)
    )
    args = {"subject": "item:1"}
    preview = await invoke("items.change", args, caller(), Surface.A2A, services=app)
    assert preview.code == "STEP_UP_REQUIRED"
    ref = preview.details["plan_ref"]
    denied = await invoke(
        "items.change", args, caller(), Surface.A2A, services=app, plan_ref=ref
    )
    assert denied == OpError("PRINCIPAL_NOT_ALLOWED")
    now = int(time.time() * 1000)
    allowed = await invoke(
        "items.change",
        args,
        caller(mfa_at_ms=now),
        Surface.CONSOLE,
        services=app,
        plan_ref=ref,
    )
    assert allowed.value == {"ok": True}
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_policy_unavailable_fails_closed() -> None:
    app, engine, _ = services(op(effect=Effect.READ))
    app = InvokeServices(
        app.registry, app.runtime, app.plans, "on", None, app.audit_write
    )
    refused = await invoke(
        "items.change", {"subject": "item:1"}, caller(), Surface.MCP, services=app
    )
    assert refused == OpError("POLICY_UNAVAILABLE")
    assert engine.calls == []


@pytest.mark.asyncio
async def test_fleet_effect_resolver_is_required_and_can_elevate_to_plan() -> None:
    fleet = op(id="fleet.call", effect=Effect.WRITE)
    app, engine, _ = services(fleet)
    args = {"subject": "item:1"}
    absent = await invoke("fleet.call", args, caller(), Surface.MCP, services=app)
    assert absent.code == "UNAVAILABLE"

    async def effect(_op: OpSpec, _params: dict[str, Any], _caller: VerifiedCaller):
        return Effect.DESTRUCTIVE, Confirm.PLAN, PrincipalRule.HUMAN

    app = InvokeServices(
        app.registry, app.runtime, app.plans, "off", None, app.audit_write, effect
    )
    preview = await invoke("fleet.call", args, caller(), Surface.MCP, services=app)
    assert preview.code == "CONFIRMATION_REQUIRED"
    assert engine.calls == []


@pytest.mark.asyncio
async def test_service_fleet_call_checks_domain_subject_grants_and_audits_owner() -> (
    None
):
    class FleetRuntime(FakeRuntime):
        async def check_subject_access(
            self, caller: VerifiedCaller, subject: str
        ) -> bool:
            self.checked_subjects.append(subject)
            return subject == "component:1"

        async def dispatch(
            self, op: OpSpec, params: dict[str, Any], context: Any
        ) -> dict[str, bool]:
            assert context.service_identity
            assert context.fleet_decision.subject_id == "component:1"
            assert context.fleet_decision.credential_mode == "service"
            assert context.owner_ref == (
                "principal:sha256:" + hashlib.sha256(b"user:1").hexdigest()
            )
            return await super().dispatch(op, params, context)

    fleet = op(
        id="fleet.call",
        params=FleetParams,
        effect=Effect.WRITE,
        scopes=frozenset({"mcp:delegate"}),
        idempotency=Idempotency.NATURAL,
    )
    app, engine, events = services(fleet, FleetRuntime())

    async def decision(op: OpSpec, params: dict[str, Any], who: VerifiedCaller):
        return FleetCallDecision(
            Effect.READ,
            Confirm.NONE,
            PrincipalRule.ANY,
            Executor.SERVICE,
            frozenset({"finance:alerts"}),
            frozenset({"node:write"}),
            "component:1",
            "service",
        )

    app = InvokeServices(
        app.registry,
        app.runtime,
        app.plans,
        "off",
        None,
        app.audit_write,
        fleet_effect=decision,
        audit_preflight=app.audit_preflight,
    )
    args = {"server": "finance", "tool": "alerts", "arguments": {}}
    denied = await invoke(
        "fleet.call",
        args,
        caller(scopes=frozenset({"mcp:delegate"})),
        Surface.MCP,
        services=app,
    )
    assert denied == OpError("SCOPE_REQUIRED", {"missing_scopes": ["finance:alerts"]})
    assert engine.checked_subjects == []
    engine.service_scopes = frozenset()
    absent_grant = await invoke(
        "fleet.call",
        args,
        caller(scopes=frozenset({"mcp:delegate", "finance:alerts"})),
        Surface.MCP,
        services=app,
    )
    assert absent_grant.code == "UNAVAILABLE"
    assert engine.calls == []
    engine.service_scopes = frozenset({"node:write"})
    approved = await invoke(
        "fleet.call",
        args,
        caller(scopes=frozenset({"mcp:delegate", "finance:alerts"})),
        Surface.MCP,
        services=app,
    )
    assert approved.value == {"ok": True}
    assert engine.checked_subjects == ["component:1"]
    assert engine.calls == [("svc:graph-os", "user:1")]
    assert events[0]["target"] == "finance/alerts"
    assert events[0]["owner"] == "user:1"


@pytest.mark.asyncio
async def test_audit_preflight_fails_before_plan_consumption_or_dispatch() -> None:
    app, engine, _ = services(op())
    args = {"subject": "item:1"}
    preview = await invoke("items.change", args, caller(), Surface.MCP, services=app)
    ref = preview.details["plan_ref"]
    app = InvokeServices(
        app.registry, app.runtime, app.plans, "off", None, app.audit_write
    )
    refused = await invoke(
        "items.change", args, caller(), Surface.MCP, services=app, plan_ref=ref
    )
    assert refused.code == "UNAVAILABLE"
    assert engine.calls == []
    assert app.plans._leases.rows[ref]["status"] == "active"


@pytest.mark.asyncio
async def test_service_client_binds_verified_service_claims() -> None:
    bound: list[dict[str, Any]] = []

    class Client:
        @contextmanager
        def use_verified_context(self, claims: dict[str, Any]):
            bound.append(claims)
            yield self

    async def get_client(tenant: str) -> Client:
        return Client()

    async def check_access(
        client: Client, caller: VerifiedCaller, subject: str
    ) -> bool:
        return True

    async def eg_dispatch(binding: Any, params: dict[str, Any], context: Any) -> Any:
        return {"ok": True}

    runtime = BoundOperationRuntime(
        caller_client=get_client,
        service_client=get_client,
        service_claims=lambda tenant: {
            "principal": "svc:graph-os",
            "tenant": tenant,
            "scopes": ["node:write"],
        },
        check_access=check_access,
        eg_dispatch=eg_dispatch,
        service_scopes=frozenset({"node:write"}),
    )
    async with runtime.as_service("t1"):
        assert bound[-1]["principal"] == "svc:graph-os"
    with pytest.raises(PermissionError):
        bad = BoundOperationRuntime(
            caller_client=get_client,
            service_client=get_client,
            service_claims=lambda tenant: {
                "principal": "user:1",
                "tenant": tenant,
                "scopes": ["node:write"],
            },
            check_access=check_access,
            eg_dispatch=eg_dispatch,
            service_scopes=frozenset({"node:write"}),
        )
        async with bad.as_service("t1"):
            pass


@pytest.mark.asyncio
async def test_cancelled_mutation_audit_is_indeterminate() -> None:
    class CancelRuntime(FakeRuntime):
        async def dispatch(
            self, op: OpSpec, params: dict[str, Any], context: Any
        ) -> Any:
            raise asyncio.CancelledError

    app, _, events = services(
        op(effect=Effect.WRITE, confirm=Confirm.NONE, idempotency=Idempotency.NATURAL),
        runtime=CancelRuntime(),
    )
    with pytest.raises(asyncio.CancelledError):
        await invoke(
            "items.change",
            {"subject": "item:1"},
            caller(),
            Surface.MCP,
            services=app,
        )
    assert events[0]["result_status"] == "INDETERMINATE"
