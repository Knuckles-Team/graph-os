"""The serving root binds one registry to explicit execution authorities."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api import serving
from graph_os.api.invoke import VerifiedCaller, invoke
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.policy import PolicyGate
from graph_os.api.registry import (
    EgMethod,
    EgSchemaRef,
    Executor,
    OpSpec,
    Registry,
    SubjectRef,
    Surface,
    Verb,
)


class Params(BaseModel):
    subject: str


class Result(BaseModel):
    subject: str


class Client:
    @contextmanager
    def use_verified_context(self, claims: dict[str, Any]):
        self.claims = claims
        yield


class Leases:
    async def issue(self, **kwargs: Any) -> None:
        pass

    async def get(self, **kwargs: Any) -> None:
        pass

    async def transition(self, **kwargs: Any) -> None:
        pass


class PlanClient:
    control_leases = Leases()


def op(**changes: Any) -> OpSpec:
    fields = dict(
        id="items.get",
        verb=Verb.ASK,
        summary="Read an item",
        examples=("Show this item",),
        params=Params,
        result=Result,
        binding=EgMethod(service="Items", op="Get"),
        scopes=frozenset({"items:read"}),
    )
    fields.update(changes)
    return OpSpec(**fields)


def ports(calls: list[tuple[str, str]]) -> serving.ServingPorts:
    async def client(tenant: str) -> Client:
        calls.append(("client", tenant))
        return Client()

    async def check_access(
        client: Client, caller: VerifiedCaller, subject: str
    ) -> bool:
        return caller.tenant == "t1" and subject == "item:1"

    async def dispatch(binding: EgMethod, params: dict[str, str], context: Any) -> Any:
        calls.append((context.owner, context.client.claims["principal"]))
        return {"subject": params["subject"]}

    async def audit(event: Any, audit_class: Any) -> None:
        calls.append(("audit", str(audit_class)))

    async def audit_preflight(event: Any, audit_class: Any) -> str:
        calls.append(("audit_preflight", str(audit_class)))
        return "audit:durable:1"

    def service_claims(tenant: str) -> dict[str, Any]:
        return {
            "principal": "svc:graph-os",
            "tenant": tenant,
            "scopes": ["items:service"],
        }

    async def fleet_effect(operation: Any, params: Any, caller: Any) -> Any:
        raise AssertionError("not a fleet call")

    return serving.ServingPorts(
        caller_client=client,
        service_client=client,
        service_claims=service_claims,
        check_access=check_access,
        eg_dispatch=dispatch,
        service_scopes=frozenset({"items:service"}),
        plan_client=PlanClient(),
        plan_seal_key=b"k" * 32,
        policy_gate=PolicyGate("none"),
        audit_preflight=audit_preflight,
        audit_write=audit,
        schema_validate=lambda ref, params: params,
        fleet_effect=fleet_effect,
        bindings={},
    )


@pytest.mark.asyncio
async def test_serving_factory_dispatches_under_verified_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([op()]))
    calls: list[tuple[str, str]] = []
    bound_ports = ports(calls)
    services = serving.build_invoke_services(bound_ports)
    assert services.audit_preflight is bound_ports.audit_preflight
    assert services.runtime._service_claims is bound_ports.service_claims
    assert services.runtime.bindings["invoke_services"] is services
    caller = VerifiedCaller(
        principal="user:1",
        tenant="t1",
        effective_scopes=frozenset({"items:read"}),
        engine_claims={
            "principal": "user:1",
            "tenant": "t1",
            "scopes": ["items:read"],
        },
        principal_kind="human",
    )
    result = await invoke(
        "items.get", {"subject": "item:1"}, caller, Surface.MCP, services=services
    )
    assert result.value == {"subject": "item:1"}
    assert calls == [("client", "t1"), ("user:1", "user:1")]


def test_missing_ports_and_service_grants_fail_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([op()]))
    basic = ports([])
    with pytest.raises(ValueError, match="plan_client"):
        serving.build_invoke_services(replace(basic, plan_client=object()))
    with pytest.raises(ValueError, match="plan_seal_key"):
        serving.build_invoke_services(replace(basic, plan_seal_key=b"short"))
    with pytest.raises(ValueError, match="policy_gate"):
        serving.build_invoke_services(replace(basic, policy_gate=None))
    with pytest.raises(ValueError, match="eg_dispatch"):
        serving.build_invoke_services(replace(basic, eg_dispatch=None))
    with pytest.raises(ValueError, match="service_claims"):
        serving.build_invoke_services(replace(basic, service_claims=None))
    with pytest.raises(ValueError, match="audit_preflight"):
        serving.build_invoke_services(replace(basic, audit_preflight=None))

    service_op = op(
        executor=Executor.SERVICE,
        executor_scopes=frozenset({"items:service", "items:extra"}),
        subject=SubjectRef(path="subject"),
    )
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([service_op]))
    with pytest.raises(ValueError, match="service grants unavailable"):
        serving.build_invoke_services(basic)


def test_missing_generated_registry_stops_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> Registry:
        raise ValueError("pinned wheel contract missing")

    monkeypatch.setattr(serving, "get_registry", unavailable)
    with pytest.raises(ValueError, match="pinned wheel contract missing"):
        serving.build_invoke_services(ports([]))


@pytest.mark.asyncio
async def test_served_assembly_shares_registry_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "get_registry", lambda: Registry([op()]))

    async def fleet_search(**kwargs: Any) -> list[Any]:
        return []

    def fleet_ops_factory(*args: Any) -> object:
        return object()

    def caller_for_request() -> None:
        return None

    bundle = serving.ServedApiPorts(
        serving=ports([]),
        caller_for_request=caller_for_request,
        fleet_search=fleet_search,
        fleet_ops_factory=fleet_ops_factory,
        resolver=IntentResolver(),
    )
    projection, visible, factory = serving.assemble_served_api(bundle)
    assert projection.registry is projection.services.registry
    assert projection.policy_gate is bundle.serving.policy_gate
    assert factory is fleet_ops_factory
    caller = VerifiedCaller(
        principal="user:1",
        tenant="t1",
        effective_scopes=frozenset({"items:read", "mcp:discover"}),
        engine_claims={
            "principal": "user:1",
            "tenant": "t1",
            "scopes": ["items:read", "mcp:discover"],
        },
        principal_kind="human",
    )
    assert await visible(op(), caller)
    assert not await visible(
        op(scopes=frozenset({"items:read", "secret:read"})), caller
    )


def test_served_ports_provider_requires_explicit_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serving, "_SERVED_PORTS", None)
    with pytest.raises(RuntimeError, match="not configured"):
        serving.configured_served_api_ports()
    with pytest.raises(TypeError, match="complete"):
        serving.configure_served_api_ports(None)


def test_mcp_caller_comes_from_verified_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.mcp_server import runtime

    class Actor:
        authenticated = True
        actor_type = "human"
        actor_id = "user:1"

        def ensure_credential_current(self) -> None:
            pass

    class Session:
        actor = Actor()
        tenant = "t1"
        scopes = frozenset({"items:read"})
        policy_version = "rev:1"

        def ensure_authority_current(self) -> None:
            pass

        def engine_verified_context(self) -> dict[str, Any]:
            return {
                "principal": "user:1",
                "tenant": "t1",
                "scopes": ["items:read"],
            }

    @contextmanager
    def verified_scope():
        yield Session()

    monkeypatch.setattr(runtime, "verified_tool_session_scope", verified_scope)
    caller = serving.caller_from_verified_session()
    assert caller.principal == "user:1"
    assert caller.tenant == "t1"
    assert caller.session is not None


@pytest.mark.asyncio
async def test_public_eg_adapters_use_only_validated_wheel_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = ModuleType("epistemic_graph.contract.invocation")
    seen: list[tuple[str, Any]] = []

    def validate(method: str, params: dict[str, Any]) -> dict[str, Any]:
        seen.append((method, params))
        return {"subject": params["subject"]}

    invocation.validate_method_params = validate
    monkeypatch.setitem(sys.modules, invocation.__name__, invocation)
    ref = EgSchemaRef(path="contract/schemas/method.request.json#/methods/GetItem")
    assert serving.validate_public_eg_params(ref, {"subject": "item:1"}) == {
        "subject": "item:1"
    }
    with pytest.raises(ValueError, match="schema reference"):
        serving.validate_public_eg_params(
            EgSchemaRef(path="contract/schemas/result.query.json#/methods/GetItem"), {}
        )

    class PublicClient:
        async def invoke_method(self, method: str, params: Any, **kwargs: Any) -> Any:
            seen.append((method, kwargs))
            return {"subject": params["subject"]}

    context = SimpleNamespace(
        client=PublicClient(),
        caller=SimpleNamespace(tenant="t1"),
        idempotency_key="request:1",
    )
    result = await serving.dispatch_public_eg_method(
        EgMethod(service="Items", op="GetItem"), {"subject": "item:1"}, context
    )
    assert result == {"subject": "item:1"}
    assert seen == [
        ("GetItem", {"subject": "item:1"}),
        ("GetItem", {"graph": "t1", "idempotency_key": "request:1"}),
    ]
