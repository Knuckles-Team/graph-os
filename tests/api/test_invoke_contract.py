"""Invocation contract fixtures are synthetic authorities, not provider qualification."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.invoke import (
    InvokeServices,
    OpResult,
    VerifiedCaller,
    invoke,
)
from graph_os.api.invoke.audit import EffectReservation
from graph_os.api.invoke.plan import EgPlanStore
from graph_os.api.policy.eunomia import PolicyGate
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    EgSchemaRef,
    Executor,
    Idempotency,
    OpSpec,
    PrincipalRule,
    Registry,
    SubjectRef,
    Surface,
    Verb,
)
from tests.api._support import make_verified_caller


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = "object:one"
    value: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)


caller = make_verified_caller("example:write")


def operation(**changes):
    fields = dict(
        id="example.write",
        verb=Verb.WRITE,
        summary="Fixture operation",
        examples=("fixture operation",),
        params=Params,
        result=Params,
        binding=Composite(handler="fixture.operation"),
        scopes=frozenset({"example:write"}),
        effect=Effect.WRITE,
        audit=AuditClass.EVENT,
        idempotency=Idempotency.KEY_REQUIRED,
        surfaces=frozenset(Surface),
    )
    fields.update(changes)
    return OpSpec(**fields)


class FixtureJournal:
    """Explicit single-loop test double for the owner's atomic durable contract."""

    def __init__(self):
        self.rows = {}
        self.completions = []
        self.fail_complete = False

    async def reserve(self, key, binding):
        if key in self.rows:
            previous, reservation = self.rows[key]
            return (
                reservation
                if previous == binding
                else EffectReservation(key, "conflict")
            )
        self.rows[key] = (binding, EffectReservation(key, "pending"))
        return EffectReservation(key, "acquired")

    async def complete(self, reference, outcome):
        if self.fail_complete:
            raise RuntimeError("fixture completion unavailable")
        binding, current = self.rows[reference]
        if current.state == "completed" and current.outcome != outcome:
            raise RuntimeError("fixture conflicting terminal outcome")
        self.rows[reference] = (
            binding,
            EffectReservation(reference, "completed", outcome),
        )
        self.completions.append(reference)


class FixtureLeases:
    def __init__(self):
        self.rows = {}
        self.transitions = 0

    async def issue(self, **fields):
        self.rows[fields["lease_id"]] = {**fields, "status": "active", "revision": 1}
        return {"outcome": "issued"}

    async def get(self, *, tenant, lease_id):
        row = self.rows.get(lease_id)
        return dict(row) if row and row["tenant"] == tenant else None

    async def transition(
        self, *, tenant, lease_id, expected_revision, to, idempotency_key
    ):
        row = self.rows[lease_id]
        if (
            row["tenant"] != tenant
            or row["revision"] != expected_revision
            or row["status"] != "active"
        ):
            return {"outcome": "conflict"}
        row.update(status=to, revision=row["revision"] + 1)
        self.transitions += 1
        return {"outcome": "applied"}


class FixtureRuntime:
    service_scopes = frozenset({"example:execute"})
    bindings: dict[str, Any] = {}

    def __init__(self):
        self.calls = []
        self.verifications = 0
        self.on_verify = None
        self.on_acquire = None
        self.on_dispatch = None
        self.subject_allowed = True
        self.subject_calls = []

    async def verify_current(self, supplied):
        self.verifications += 1
        return (
            self.on_verify(supplied, self.verifications) if self.on_verify else supplied
        )

    @asynccontextmanager
    async def as_caller(self, supplied):
        if self.on_acquire:
            self.on_acquire()
        yield object()

    @asynccontextmanager
    async def as_service(self, tenant, *, required_scopes):
        assert required_scopes <= self.service_scopes
        if self.on_acquire:
            self.on_acquire()
        yield object()

    async def check_subject_access(self, supplied, subject):
        self.subject_calls.append((supplied, subject))
        return self.subject_allowed

    async def dispatch(self, op, params, context):
        self.calls.append((op, params, context))
        if self.on_dispatch:
            return await self.on_dispatch()
        return {"value": params["value"]}


class FixtureAudit:
    def __init__(self):
        self.events = []
        self.fail_reserve = False
        self.fail_write = False

    async def reserve(self, event, audit_class, supplied):
        if self.fail_reserve:
            raise RuntimeError("fixture audit unavailable")
        self.events.append(dict(event))
        return "receipt:" + supplied.request_id

    async def write(self, event, audit_class, supplied):
        if self.fail_write:
            raise RuntimeError("fixture outcome unavailable")
        self.events.append(dict(event))


def setup_services(op=None, **changes):
    runtime, audit, journal, leases = (
        FixtureRuntime(),
        FixtureAudit(),
        FixtureJournal(),
        FixtureLeases(),
    )
    fields = dict(
        registry=changes.pop("registry", None) or Registry([op or operation()]),
        runtime=runtime,
        plans=EgPlanStore(
            SimpleNamespace(control_leases=leases),
            lease_kind="fixture.plan",
            seal_key=b"x" * 32,
        ),
        policy_gate=PolicyGate("none"),
        audit_preflight=audit.reserve,
        audit_write=audit.write,
        effects=journal,
        external_effect_operations=frozenset({(op or operation()).id})
        if changes.get("effects", journal) is not None
        else frozenset(),
    )
    fields.update(changes)
    return InvokeServices(**fields), runtime, audit, journal, leases


def run(
    services,
    *,
    supplied=None,
    params=None,
    op_id="example.write",
    surface=Surface.HTTP,
    **kwargs,
):
    return asyncio.run(
        invoke(
            op_id,
            {} if params is None else params,
            caller() if supplied is None else supplied,
            surface,
            services=services,
            **kwargs,
        )
    )


@pytest.mark.spec("GRAPHOS-OPS-R036")
@pytest.mark.parametrize("surface", list(Surface))
def test_equivalent_surface_authority_and_result(surface):
    services, runtime, audit, _, _ = setup_services()
    result = run(services, surface=surface, idempotency_key="key:one")
    assert result == OpResult(value={"value": 1})
    assert len(runtime.calls) == 1
    assert runtime.calls[0][2].idempotency_key == "key:one"
    assert audit.events[0]["result_status"] == "PENDING"
    assert audit.events[1]["audit_ref"].startswith("receipt:")
    assert "value" not in audit.events[0]


@pytest.mark.parametrize(
    "params",
    [
        {"extra": 1},
        {"tenant": "other"},
        {"payload": {"_actor": "other"}},
        {"payload": {"items": [{"principal": "other"}]}},
        [],
    ],
)
@pytest.mark.spec("GRAPHOS-OPS-R036")
def test_invalid_arguments_precede_authority_and_effect(params):
    services, runtime, audit, _, _ = setup_services()
    assert (
        run(services, params=params, idempotency_key="key").code == "INVALID_ARGUMENT"
    )
    assert not runtime.calls and not audit.events and runtime.verifications == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"authenticated": False},
        {"principal_kind": "robot"},
        {"effective_scopes": frozenset()},
        {"effective_scopes": frozenset({"*"})},
        {"engine_claims": {"principal": "spoof"}},
        {"policy_revision": ""},
    ],
)
@pytest.mark.spec("GRAPHOS-OPS-R036")
def test_invalid_or_insufficient_authority_has_no_effect(changes):
    services, runtime, audit, _, _ = setup_services()
    outcome = run(services, supplied=caller(**changes), idempotency_key="key")
    assert outcome.code in {"UNAUTHENTICATED", "SCOPE_REQUIRED"}
    assert not runtime.calls and not audit.events


def test_unknown_operation_and_forbidden_surface():
    services, runtime, audit, _, _ = setup_services(
        operation(surfaces=frozenset({Surface.HTTP}))
    )
    assert run(services, op_id="unknown.op").code == "UNKNOWN_OP"
    assert run(services, surface=Surface.MCP).code == "SURFACE_NOT_ALLOWED"
    assert not runtime.calls and not audit.events


@pytest.mark.parametrize(
    "rule,kind,delegated",
    [
        (PrincipalRule.HUMAN, "service", False),
        (PrincipalRule.HUMAN_UNDELEGATED, "human", True),
        (PrincipalRule.SERVICE_ONLY, "human", False),
    ],
)
def test_policy_disabled_keeps_principal_restrictions(rule, kind, delegated):
    services, runtime, _, _, _ = setup_services(operation(principals=rule))
    result = run(
        services,
        supplied=caller(principal_kind=kind, delegated=delegated),
        idempotency_key="key",
    )
    assert result.code == "PRINCIPAL_NOT_ALLOWED" and not runtime.calls


def test_enabled_pdp_unavailable_is_not_policy_disabled():
    services, runtime, audit, _, _ = setup_services(policy_gate=PolicyGate("remote"))
    assert run(services, idempotency_key="key").code == "POLICY_UNAVAILABLE"
    assert not runtime.calls and not audit.events


@pytest.mark.parametrize(
    "port", ["runtime", "plans", "policy_gate", "audit_preflight", "audit_write"]
)
def test_missing_required_authority_fails_construction(port):
    with pytest.raises(ValueError, match="authority"):
        setup_services(**{port: None})


def test_provider_schema_requires_authority_and_validation(tmp_path):
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "fixture.json").write_text('{"Fixture": {"type": "object"}}')
    op = operation(params=EgSchemaRef(path="contract/schemas/fixture.json#/Fixture"))
    registry = Registry([op], contract_root=tmp_path)
    with pytest.raises(ValueError, match="schema authority"):
        setup_services(op, registry=registry)

    def validate(schema, params):
        if set(params) != {"value"}:
            raise ValueError("extra")
        return params

    services, runtime, _, _, _ = setup_services(
        op, registry=registry, schema_validate=validate
    )
    assert (
        run(services, params={"extra": 1}, idempotency_key="key").code
        == "INVALID_ARGUMENT"
    )
    assert not runtime.calls
    assert run(services, params={"value": 2}, idempotency_key="key").value == {
        "value": 2
    }


def test_claims_are_deeply_owned_and_authentication_has_no_default():
    original = {
        "principal": "person:one",
        "tenant": "tenant:one",
        "scopes": ["example:write"],
        "policy_version": "revision:one",
        "delegation": {"chain": ["a"]},
    }
    verified = caller(engine_claims=original, delegated=True)
    original["scopes"].append("example:admin")
    original["delegation"]["chain"].append("b")
    assert verified.engine_claims["scopes"] == ("example:write",)
    assert verified.engine_claims["delegation"]["chain"] == ("a",)
    with pytest.raises(TypeError):
        verified.engine_claims["tenant"] = "changed"
    with pytest.raises(TypeError):
        VerifiedCaller(
            principal="x",
            tenant="y",
            effective_scopes=frozenset(),
            engine_claims={},
            principal_kind="human",
        )
    assert not hasattr(VerifiedCaller, "from_session")


@pytest.mark.parametrize("stage", ["initial", "after_client"])
def test_current_authority_rechecks_block_revocation(stage):
    services, runtime, _, _, _ = setup_services()
    revoked = stage == "initial"

    def verify(supplied, count):
        return replace(supplied, authenticated=False) if revoked else supplied

    def acquire():
        nonlocal revoked
        revoked = True

    runtime.on_verify = verify
    runtime.on_acquire = acquire if stage == "after_client" else None
    assert run(services, idempotency_key="key").code == "UNAUTHENTICATED"
    assert not runtime.calls


def test_verified_refresh_may_not_broaden_or_change_identity():
    services, runtime, _, _, _ = setup_services()
    for changes in (
        {"principal": "other"},
        {"tenant": "other"},
        {"effective_scopes": frozenset({"example:write", "example:admin"})},
    ):
        runtime.on_verify = lambda supplied, count, changes=changes: caller(**changes)
        assert run(services, idempotency_key="key").code == "UNAUTHENTICATED"
    assert not runtime.calls


def test_service_subject_denial_and_caller_owner():
    op = operation(
        executor=Executor.SERVICE,
        executor_scopes=frozenset({"example:execute"}),
        subject=SubjectRef(path="subject"),
    )
    services, runtime, audit, _, _ = setup_services(op)
    runtime.subject_allowed = False
    assert run(services, idempotency_key="key").code == "SUBJECT_ACCESS_DENIED"
    assert not runtime.calls and not audit.events
    runtime.subject_allowed = True
    assert (
        run(
            services, params={"payload": {"owner_ref": "spoof"}}, idempotency_key="key"
        ).code
        == "INVALID_ARGUMENT"
    )
    assert run(services, idempotency_key="key").code == "OK"
    context = runtime.calls[0][2]
    assert context.owner == caller().principal and context.service_identity
    assert runtime.subject_calls[0][0].principal_kind == "human"
    assert context.owner_ref.startswith("principal:sha256:")


def test_audit_reservation_failure_prevents_dispatch():
    services, runtime, audit, journal, _ = setup_services()
    audit.fail_reserve = True
    assert run(services, idempotency_key="key").code == "UNAVAILABLE"
    assert not runtime.calls and not journal.rows
