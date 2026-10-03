"""Effects and authority integration with explicit synthetic owner fixtures."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_invoke_contract import caller, operation, run, setup_services

from graph_os.api.invoke import FleetCallDecision, invoke
from graph_os.api.invoke.eg_audit import EgAuditAdapter
from graph_os.api.invoke.executor import BoundOperationRuntime
from graph_os.api.invoke.plan import bind_plan
from graph_os.api.policy.eunomia import PolicyGate
from graph_os.api.registry import (
    AuditClass,
    Confirm,
    Effect,
    Executor,
    Idempotency,
    PrincipalRule,
    Surface,
)


def test_cross_surface_durable_replay_and_changed_params_conflict():
    services, runtime, _, journal, _ = setup_services()
    first = run(services, idempotency_key="same", surface=Surface.HTTP)
    assert run(services, idempotency_key="same", surface=Surface.MCP) == first
    assert (
        run(services, idempotency_key="same", params={"value": 2}).code
        == "INVALID_ARGUMENT"
    )
    assert len(runtime.calls) == 1 and len(journal.rows) == 1


def test_completed_replay_still_requires_current_authority():
    services, runtime, _, _, _ = setup_services()
    assert run(services, idempotency_key="same").code == "OK"
    runtime.on_verify = lambda supplied, count: replace(supplied, authenticated=False)
    assert run(services, idempotency_key="same").code == "UNAUTHENTICATED"
    assert len(runtime.calls) == 1


def test_concurrent_reservation_has_one_effect_and_pending_second_attempt():
    async def scenario():
        services, runtime, _, _, _ = setup_services()
        started, finish = asyncio.Event(), asyncio.Event()

        async def dispatch():
            started.set()
            await finish.wait()
            return "one effect"

        runtime.on_dispatch = dispatch
        first = asyncio.create_task(
            invoke(
                "example.write",
                {},
                caller(),
                Surface.HTTP,
                services=services,
                idempotency_key="same",
            )
        )
        await started.wait()
        second = await invoke(
            "example.write",
            {},
            caller(),
            Surface.MCP,
            services=services,
            idempotency_key="same",
        )
        assert second.code == "INDETERMINATE"
        finish.set()
        assert (await first).value == "one effect"
        assert len(runtime.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["dispatch", "audit", "completion", "timeout"])
def test_uncertain_outcome_stays_pending_without_redispatch(failure):
    services, runtime, audit, journal, _ = setup_services()

    async def dispatch():
        if failure == "timeout":
            raise TimeoutError("possibly dispatched")
        raise RuntimeError("possibly dispatched")

    if failure in {"dispatch", "timeout"}:
        runtime.on_dispatch = dispatch
    audit.fail_write = failure == "audit"
    journal.fail_complete = failure == "completion"
    first = run(services, idempotency_key="same")
    assert first.code == "INDETERMINATE" and first.details["audit_ref"]
    assert run(services, idempotency_key="same").code == "INDETERMINATE"
    assert len(runtime.calls) == 1
    assert all(
        reservation.state == "pending" for _, reservation in journal.rows.values()
    )


def test_cancellation_retains_pending_and_does_not_complete():
    async def scenario():
        services, runtime, audit, journal, _ = setup_services()
        started = asyncio.Event()

        async def dispatch():
            started.set()
            await asyncio.Event().wait()

        runtime.on_dispatch = dispatch
        task = asyncio.create_task(
            invoke(
                "example.write",
                {},
                caller(),
                Surface.HTTP,
                services=services,
                idempotency_key="same",
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not journal.completions
        assert audit.events[-1]["result_status"] == "INDETERMINATE"
        assert (
            await invoke(
                "example.write",
                {},
                caller(),
                Surface.HTTP,
                services=services,
                idempotency_key="same",
            )
        ).code == "INDETERMINATE"
        assert len(runtime.calls) == 1

    asyncio.run(scenario())


def test_natural_write_does_not_require_client_key():
    services, runtime, _, _, _ = setup_services(
        operation(idempotency=Idempotency.NATURAL)
    )
    assert run(services).code == "OK"
    assert (
        run(services, supplied=caller(request_id="another-transport-request")).code
        == "OK"
    )
    assert len(runtime.calls) == 1


def test_missing_or_oversized_client_key_refuses_write():
    services, runtime, _, _, _ = setup_services()
    for key in (None, "", "x" * 257, 42):
        assert run(services, idempotency_key=key).code == "INVALID_ARGUMENT"
    assert not runtime.calls


def test_missing_durable_coverage_fails_closed_but_native_owner_is_supported():
    services, runtime, _, _, _ = setup_services(effects=None)
    assert run(services, idempotency_key="same").code == "UNAVAILABLE"
    assert not runtime.calls
    # This fixture represents an existing owner that atomically implements replay;
    # it is deliberately not installed as a production GraphOS journal.
    services = replace(
        services, native_idempotency={"example.write": "fixture:OperationIdentity"}
    )
    receipts = {}

    async def native_dispatch(op, params, context):
        if context.idempotency_key not in receipts:
            runtime.calls.append((op, params, context))
            receipts[context.idempotency_key] = {"receipt": 1}
        return receipts[context.idempotency_key]

    runtime.dispatch = native_dispatch
    assert run(services, idempotency_key="same").value == {"receipt": 1}
    assert run(services, idempotency_key="same").value == {"receipt": 1}
    assert len(runtime.calls) == 1


def test_destructive_plan_single_consumption_and_replay():
    services, runtime, audit, _, leases = setup_services(
        operation(effect=Effect.DESTRUCTIVE)
    )
    preview = run(services)
    assert (
        preview.code == "CONFIRMATION_REQUIRED"
        and not runtime.calls
        and not audit.events
    )
    reference = preview.details["plan_ref"]
    assert (
        leases.rows[reference]["expires_at_ms"] - leases.rows[reference]["issued_at_ms"]
        == 600_000
    )
    first = run(services, plan_ref=reference)
    assert first.code == "OK"
    assert run(services, plan_ref=reference) == first
    assert leases.transitions == 1 and len(runtime.calls) == 1


@pytest.mark.parametrize(
    "changed", ["params", "principal", "tenant", "policy", "registry", "expired"]
)
def test_plan_context_changes_refuse_before_effect(changed):
    services, runtime, _, _, leases = setup_services(
        operation(effect=Effect.DESTRUCTIVE)
    )
    preview = run(services)
    reference = preview.details["plan_ref"]
    kwargs = {"plan_ref": reference}
    if changed == "params":
        kwargs["params"] = {"value": 2}
    elif changed in {"principal", "tenant"}:
        kwargs["supplied"] = caller(**{changed: "changed"})
    elif changed == "policy":
        kwargs["supplied"] = caller(policy_revision="revision:two")
    elif changed == "registry":
        leases.rows[reference]["grant"]["registry_digest"] = "stale"
    else:
        leases.rows[reference]["hard_expires_at_ms"] = 0
    assert run(services, **kwargs).code in {
        "PLAN_MISMATCH",
        "PLAN_STALE",
        "PLAN_EXPIRED",
    }
    assert not runtime.calls


def test_fuzzy_write_only_previews():
    services, runtime, audit, _, _ = setup_services()
    result = run(services, resolved_from_intent=True, idempotency_key="same")
    assert result.code == "CONFIRMATION_REQUIRED"
    assert not runtime.calls and not audit.events


def test_console_admin_requires_same_human_fresh_mfa_and_session():
    op = operation(effect=Effect.ADMIN, principals=PrincipalRule.HUMAN_UNDELEGATED)
    services, runtime, _, _, _ = setup_services(op)
    reference = run(services).details["plan_ref"]
    assert (
        run(services, plan_ref=reference, surface=Surface.MCP).code
        == "PRINCIPAL_NOT_ALLOWED"
    )
    assert (
        run(services, plan_ref=reference, surface=Surface.CONSOLE).code
        == "PRINCIPAL_NOT_ALLOWED"
    )
    for changes in (
        {"mfa_at_ms": 1},
        {"mfa_at_ms": int(time.time() * 1000) + 10000},
        {"mfa_at_ms": int(time.time() * 1000), "credential_kind": "api_key"},
    ):
        assert (
            run(
                services,
                plan_ref=reference,
                surface=Surface.CONSOLE,
                supplied=caller(**changes),
            ).code
            == "PRINCIPAL_NOT_ALLOWED"
        )
    assert not runtime.calls
    assert (
        run(
            services,
            plan_ref=reference,
            surface=Surface.CONSOLE,
            supplied=caller(mfa_at_ms=int(time.time() * 1000)),
        ).code
        == "OK"
    )


def test_invalid_destructive_metadata_cannot_disable_confirmation():
    services, runtime, _, _, _ = setup_services(
        operation(effect=Effect.DESTRUCTIVE, confirm=Confirm.NONE)
    )
    assert (
        run(services, idempotency_key="key").code == "UNAVAILABLE" and not runtime.calls
    )


def fleet_decision(**changes):
    fields = dict(
        effect=Effect.WRITE,
        confirm=Confirm.NONE,
        principals=PrincipalRule.ANY,
        executor=Executor.CALLER,
        required_scopes=frozenset({"example:write"}),
        executor_scopes=frozenset(),
        subject_id=None,
        credential_mode="delegated",
    )
    fields.update(changes)
    return FleetCallDecision(**fields)


def test_fleet_tuple_fallback_and_unknown_decisions_refuse():
    for invalid in (
        (Effect.WRITE, Confirm.NONE, PrincipalRule.ANY),
        None,
        fleet_decision(required_scopes=frozenset({"*"})),
    ):

        async def effect(op, params, supplied, invalid=invalid):
            return invalid

        services, runtime, _, _, _ = setup_services(
            operation(id="fleet.call"), fleet_effect=effect
        )
        assert (
            run(services, op_id="fleet.call", idempotency_key="same").code
            == "UNAVAILABLE"
        )
        assert not runtime.calls


def test_effective_fleet_metadata_is_checked_by_policy_and_at_dispatch():
    calls = []

    async def effect(op, params, supplied):
        return fleet_decision(effect=Effect.DESTRUCTIVE, confirm=Confirm.PLAN)

    class Policy:
        async def check_op(self, op, supplied):
            calls.append(op.effect)
            return op.effect != Effect.DESTRUCTIVE

    services, runtime, _, _, _ = setup_services(
        operation(id="fleet.call"), fleet_effect=effect, policy_gate=Policy()
    )
    assert (
        run(services, op_id="fleet.call", idempotency_key="same").code
        == "POLICY_DENIED"
    )
    assert calls == [Effect.WRITE, Effect.DESTRUCTIVE] and not runtime.calls
    counter = 0

    async def changing_effect(op, params, supplied):
        nonlocal counter
        counter += 1
        return fleet_decision(
            required_scopes=frozenset({"example:write"})
            if counter == 1
            else frozenset()
        )

    services = replace(
        services, fleet_effect=changing_effect, policy_gate=PolicyGate("none")
    )
    assert (
        run(services, op_id="fleet.call", idempotency_key="same").code
        == "POLICY_DENIED"
    )
    assert not runtime.calls


def test_bound_service_execution_uses_configured_principal_and_exact_scopes():
    seen = []

    @contextmanager
    def context(claims):
        seen.append(claims)
        yield

    async def client(tenant):
        return SimpleNamespace(use_verified_context=context)

    supplied_claims = {
        "tenant": "tenant:one",
        "principal": "svc:configured",
        "scopes": ["example:execute"],
    }

    async def claims(tenant, required):
        return supplied_claims

    async def verify(supplied):
        return supplied

    async def check_access(client, supplied, subject):
        return True

    async def dispatch(binding, params, context):
        return None

    original_bindings = {"admitted": object()}
    runtime = BoundOperationRuntime(
        caller_client=client,
        service_client=client,
        service_claims=claims,
        verify_current=verify,
        check_access=check_access,
        eg_dispatch=dispatch,
        service_scopes=frozenset({"example:execute", "example:other"}),
        service_principal="svc:configured",
        bindings=original_bindings,
    )

    original_bindings["unreviewed"] = object()
    assert set(runtime.bindings) == {"admitted"}
    with pytest.raises(TypeError):
        runtime.bindings["unreviewed"] = object()
    with pytest.raises(AttributeError):
        runtime.bindings = {}

    async def execute():
        async with runtime.as_service(
            "tenant:one", required_scopes=frozenset({"example:execute"})
        ):
            pass

    asyncio.run(execute())
    assert seen[0]["principal"] == "svc:configured"
    for key, value in (
        ("principal", "svc:graph-os"),
        ("tenant", "other"),
        ("scopes", ["example:execute", "example:other"]),
    ):
        original = supplied_claims[key]
        supplied_claims[key] = value
        with pytest.raises(PermissionError):
            asyncio.run(execute())
        supplied_claims[key] = original
    assert len(seen) == 1


def test_eg_audit_uses_actual_keyword_and_links_receipt(monkeypatch):
    receipts = []
    override = {}

    async def append(*, op, surface, params_sha256, status, request_id, audit_class):
        receipts.append((status, audit_class))
        return {
            "graph": "tenant:one",
            "seq": 7 if status == "reserved" else 8,
            "entry_sha256": "a" * 64,
            "replayed": False,
            "reservation_seq": 7,
            "outcome_seq": None if status == "reserved" else 8,
            **override,
        }

    @contextmanager
    def context(claims):
        assert claims["principal"] == "person:one"
        yield

    async def client(tenant):
        return SimpleNamespace(
            admin=SimpleNamespace(audit_append=append), use_verified_context=context
        )

    adapter = EgAuditAdapter(client)
    event = {
        "tenant": "tenant:one",
        "principal": "person:one",
        "plan_digest": "b" * 64,
        "op": "example.write",
        "surface": "http",
        "request_id": "request:one",
    }
    reference = asyncio.run(adapter.preflight(event, AuditClass.EVENT, caller()))
    asyncio.run(
        adapter.write(
            {**event, "audit_ref": reference, "result_status": "OK"},
            AuditClass.EVENT,
            caller(),
        )
    )
    assert receipts == [("reserved", "event"), ("ok", "event")]
    override["reservation_seq"] = 6
    with pytest.raises(RuntimeError, match="different reservation"):
        asyncio.run(
            adapter.write(
                {**event, "audit_ref": reference, "result_status": "OK"},
                AuditClass.EVENT,
                caller(),
            )
        )
    for invalid in (
        {"seq": True},
        {"graph": "other"},
        {"entry_sha256": "bad"},
        {"replayed": 1},
        {"outcome_seq": -1},
    ):
        override.clear()
        override.update(invalid)
        with pytest.raises(RuntimeError):
            asyncio.run(adapter.preflight(event, AuditClass.EVENT, caller()))
    before = len(receipts)
    asyncio.run(
        adapter.write(
            {**event, "audit_ref": reference, "result_status": "INDETERMINATE"},
            AuditClass.EVENT,
            caller(),
        )
    )
    assert len(receipts) == before


def test_real_http_projection_reaches_invoke_and_durable_replay():
    import httpx

    from graph_os.api.http.app import create_api_application

    services, runtime, _, _, _ = setup_services()

    class Auth:
        async def authenticate(self, request):
            return caller()

        def is_console_request(self, request, supplied):
            return False

    app = create_api_application(
        services=services,
        visibility=services.policy_gate.check_op,
        authenticator=Auth(),
        invoke=invoke,
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            for _ in range(2):
                response = await client.post(
                    "/ops/example.write",
                    json={"value": 3},
                    headers={"Idempotency-Key": "same"},
                )
                assert response.status_code == 200
                assert response.json()["result"] == {"value": 3}
            bad = await client.post(
                "/ops/example.write",
                json={"tenant": "spoof"},
                headers={"Idempotency-Key": "other"},
            )
            assert bad.status_code == 400
        assert len(runtime.calls) == 1

    asyncio.run(scenario())


def test_replay_rechecks_authority_after_awaiting_audit():
    services, runtime, audit, _, _ = setup_services()
    assert run(services, idempotency_key="same").code == "OK"

    async def reserve(event, audit_class, supplied):
        runtime.on_verify = lambda caller, count: replace(caller, authenticated=False)
        return await audit.reserve(event, audit_class, supplied)

    services = replace(services, audit_preflight=reserve)
    assert run(services, idempotency_key="same").code == "UNAUTHENTICATED"
    assert len(runtime.calls) == 1


def test_dispatch_target_is_immutable_after_validation():
    async def effect(op, params, supplied):
        with pytest.raises(TypeError):
            params["payload"]["tool"] = "changed"
        return fleet_decision()

    services, runtime, _, _, _ = setup_services(
        operation(id="fleet.call"), fleet_effect=effect
    )
    assert (
        run(
            services,
            op_id="fleet.call",
            params={"payload": {"tool": "original"}},
            idempotency_key="same",
        ).code
        == "OK"
    )
    assert runtime.calls[0][1]["payload"]["tool"] == "original"


def test_request_snapshot_precedes_first_authority_await():
    params = {"payload": {"target": {"name": "original"}}}
    services, runtime, _, _, _ = setup_services()

    def verify(supplied, count):
        params["payload"]["target"]["name"] = "changed"
        return supplied

    runtime.on_verify = verify
    assert run(services, params=params, idempotency_key="same").code == "OK"
    assert runtime.calls[0][1]["payload"]["target"]["name"] == "original"


def test_audit_surface_identity_is_distinct_from_effect_identity():
    verified = caller(request_id="stable-effect-key")
    first = EgAuditAdapter._request_id({"surface": "http"}, verified)
    second = EgAuditAdapter._request_id({"surface": "mcp"}, verified)
    assert first != second
    assert first == EgAuditAdapter._request_id({"surface": "http"}, verified)


def test_native_planned_effect_without_coordinator_is_unavailable_before_consumption():
    services, runtime, audit, _, leases = setup_services(
        operation(effect=Effect.DESTRUCTIVE),
        effects=None,
        native_idempotency={"example.write": "fixture:OperationIdentity"},
    )
    assert run(services).code == "UNAVAILABLE"
    # A legacy caller may already hold a valid plan. Its presence does not add
    # a replay-only native-owner contract that the provider does not expose.
    params = {"subject": "object:one", "value": 1, "payload": {}}
    binding = bind_plan(
        services.registry.get("example.write"),
        params,
        caller(),
        services.registry.digest,
    )
    reference = asyncio.run(services.plans.issue(binding, params))
    assert run(services, plan_ref=reference).code == "UNAVAILABLE"
    assert run(services, plan_ref=reference).code == "UNAVAILABLE"
    assert not runtime.calls and not audit.events and leases.transitions == 0
    # An already-consumed plan does not prove the old mutation completed.
    leases.rows[reference]["status"] = "consumed"
    assert run(services, plan_ref=reference).code == "UNAVAILABLE"
    assert not runtime.calls


def test_native_planned_effect_replays_only_through_existing_coordinator():
    services, runtime, _, journal, leases = setup_services(
        operation(effect=Effect.DESTRUCTIVE),
        native_idempotency={"example.write": "fixture:OperationIdentity"},
    )
    reference = run(services).details["plan_ref"]
    first = run(services, plan_ref=reference)
    assert first.code == "OK"
    assert run(services, plan_ref=reference) == first
    assert len(runtime.calls) == 1 and leases.transitions == 1
    assert len(journal.rows) == 1
    assert runtime.calls[0][2].idempotency_key == "plan:" + reference
    assert (
        run(services, plan_ref=reference, params={"value": 2}).code
        == "INVALID_ARGUMENT"
    )
    assert len(runtime.calls) == 1
    runtime.on_verify = lambda supplied, count: replace(supplied, authenticated=False)
    assert run(services, plan_ref=reference).code == "UNAUTHENTICATED"
    assert leases.transitions == 1 and len(runtime.calls) == 1


def test_native_planned_unknown_outcome_remains_pending_without_plan_reconsumption():
    services, runtime, _, journal, leases = setup_services(
        operation(effect=Effect.DESTRUCTIVE),
        native_idempotency={"example.write": "fixture:OperationIdentity"},
    )

    async def dispatch():
        raise TimeoutError("native effect may have committed")

    runtime.on_dispatch = dispatch
    reference = run(services).details["plan_ref"]
    assert run(services, plan_ref=reference).code == "INDETERMINATE"
    assert run(services, plan_ref=reference).code == "INDETERMINATE"
    assert leases.transitions == 1 and len(runtime.calls) == 1
    assert all(item.state == "pending" for _, item in journal.rows.values())


def test_external_effect_coordinator_has_exact_per_operation_coverage():
    from graph_os.api.registry import Registry

    registry = Registry([operation(), operation(id="example.other")])
    services, runtime, audit, journal, leases = setup_services(
        registry=registry,
        native_idempotency={"example.write": "fixture:OperationIdentity"},
        external_effect_operations=frozenset({"example.other"}),
    )
    assert run(services, resolved_from_intent=True).code == "UNAVAILABLE"
    assert (
        not runtime.calls and not audit.events and not journal.rows and not leases.rows
    )
    assert run(services, op_id="example.other", idempotency_key="same").code == "OK"
    assert len(runtime.calls) == 1
    services = replace(services, native_idempotency={})
    assert run(services, idempotency_key="same").code == "UNAVAILABLE"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize(
    "coverage", [frozenset({"unknown.op"}), frozenset({"*"}), {"example.write"}]
)
def test_invalid_external_effect_coverage_refuses_construction(coverage):
    with pytest.raises(ValueError, match="exact operations"):
        setup_services(external_effect_operations=coverage)


def test_context_service_map_is_an_owned_immutable_snapshot():
    from graph_os.api.invoke.executor import ExecutionContext

    source = {"admitted": object()}
    admitted = source["admitted"]
    context = ExecutionContext(caller(), object(), "person:one", False, source)
    source["unreviewed"] = object()
    assert context.services == {"admitted": admitted}
    with pytest.raises(TypeError):
        context.services["unreviewed"] = object()
