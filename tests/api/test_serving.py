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

    async def audit(event: Any, audit_class: Any, actor: VerifiedCaller) -> None:
        calls.append(("audit", str(audit_class)))

    async def audit_preflight(
        event: Any, audit_class: Any, actor: VerifiedCaller
    ) -> str:
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


def test_harness_endpoint_requires_export_port_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.api.ops.harness import specs

    monkeypatch.setattr(serving, "get_registry", lambda: Registry(specs()))
    basic = ports([])
    with pytest.raises(ValueError, match="context_endpoint_export"):
        serving.build_invoke_services(basic)

    async def export() -> tuple[object, object]:
        return object(), object()

    services = serving.build_invoke_services(
        replace(basic, context_endpoint_export=export)
    )
    assert services.runtime.bindings["context_endpoint_export"] is export


@pytest.mark.asyncio
async def test_harness_endpoint_invokes_only_for_scoped_service_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.layers.contracts import McpEndpoint

    from graph_os.api.harness_context import ContextCapabilityProof
    from graph_os.api.ops.harness import specs

    monkeypatch.setattr(serving, "get_registry", lambda: Registry(specs()))

    async def export() -> tuple[McpEndpoint, ContextCapabilityProof]:
        return (
            McpEndpoint(
                name="epistemic-graph-context",
                url="https://graph.example/mcp",
                bearer_ref="env://GRAPHOS_CONTEXT_TOKEN",
            ),
            ContextCapabilityProof(
                endpoint_url="https://graph.example/mcp",
                registry_digest="a" * 64,
                tools=frozenset({"find", "ask"}),
                operations=frozenset({"context.view", "query.uql"}),
            ),
        )

    services = serving.build_invoke_services(
        replace(ports([]), context_endpoint_export=export)
    )

    def caller(kind: str, scopes: frozenset[str]) -> VerifiedCaller:
        return VerifiedCaller(
            principal="svc:agent-utilities" if kind == "service" else "user:1",
            tenant="t1",
            principal_kind=kind,
            effective_scopes=scopes,
            engine_claims={
                "principal": "svc:agent-utilities" if kind == "service" else "user:1",
                "tenant": "t1",
                "scopes": sorted(scopes),
            },
        )

    granted = frozenset({"mcp:discover", "mcp:delegate"})
    allowed = await invoke(
        "harness.context_endpoint",
        {},
        caller("service", granted),
        Surface.HTTP,
        services=services,
    )
    assert allowed.value["endpoint"]["bearer_ref"] == "env://GRAPHOS_CONTEXT_TOKEN"
    human = await invoke(
        "harness.context_endpoint",
        {},
        caller("human", granted),
        Surface.HTTP,
        services=services,
    )
    assert human.code == "PRINCIPAL_NOT_ALLOWED"
    missing_scope = await invoke(
        "harness.context_endpoint",
        {},
        caller("service", frozenset({"mcp:discover"})),
        Surface.HTTP,
        services=services,
    )
    assert missing_scope.code == "SCOPE_REQUIRED"


def test_harness_export_bootstrap_serves_http_reference_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.layers.contracts import McpEndpoint
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from graph_os.api.harness_context import ContextCapabilityProof
    from graph_os.api.http import create_api_application
    from graph_os.api.ops.harness import specs

    monkeypatch.setattr(serving, "_SERVED_PORTS", None)
    monkeypatch.setattr(serving, "get_registry", lambda: Registry(specs()))
    calls: list[str] = []

    async def resolve_bearer(reference: str) -> str:
        calls.append(f"resolve:{reference}")
        return "secret-value-never-exported"

    async def configured_export(
        *, bearer_ref: str, resolve_bearer: Any
    ) -> tuple[McpEndpoint, ContextCapabilityProof]:
        calls.append(f"probe:{bearer_ref}")
        assert await resolve_bearer(bearer_ref) == "secret-value-never-exported"
        url = "https://graph.example/mcp"
        return (
            McpEndpoint(
                name="epistemic-graph-context",
                url=url,
                bearer_ref=bearer_ref,
            ),
            ContextCapabilityProof(
                endpoint_url=url,
                registry_digest="a" * 64,
                tools=frozenset({"ask", "find"}),
                operations=frozenset({"context.view", "query.uql"}),
            ),
        )

    monkeypatch.setattr(serving, "configured_eg_context_endpoint", configured_export)
    caller = VerifiedCaller(
        principal="svc:agent-utilities",
        tenant="t1",
        principal_kind="service",
        effective_scopes=frozenset({"mcp:discover", "mcp:delegate"}),
        engine_claims={
            "principal": "svc:agent-utilities",
            "tenant": "t1",
            "scopes": ["mcp:discover", "mcp:delegate"],
        },
    )

    class Auth:
        async def authenticate(self, request: Any) -> VerifiedCaller:
            return caller

        def is_console_request(self, request: Any, actor: Any) -> bool:
            return False

    async def visible(operation: Any, actor: Any) -> bool:
        return True

    bundle = serving.ServedApiPorts(
        serving=ports([]),
        caller_for_request=lambda: caller,
        fleet_search=lambda *args: (),
        fleet_ops_factory=lambda *args: None,
        resolver=IntentResolver(),
    )
    with pytest.raises(ValueError, match="both required"):
        serving.configure_served_api_ports(
            bundle, context_bearer_ref="env://GRAPHOS_CONTEXT_TOKEN"
        )
    with pytest.raises(ValueError, match="bearer reference"):
        serving.configure_served_api_ports(
            bundle,
            context_bearer_ref="raw-secret",
            resolve_bearer=resolve_bearer,
        )
    serving.configure_served_api_ports(
        bundle,
        context_bearer_ref="env://GRAPHOS_CONTEXT_TOKEN",
        resolve_bearer=resolve_bearer,
    )
    configured = serving.configured_served_api_ports()
    services = serving.build_invoke_services(configured.serving)
    parent = FastAPI()
    parent.mount(
        "/api/v1",
        create_api_application(
            services=services,
            visibility=visible,
            authenticator=Auth(),  # type: ignore[arg-type]
        ),
    )
    with TestClient(parent) as client:
        response = client.post(
            "/api/v1/ops/harness.context_endpoint",
            headers={"Authorization": "Bearer verified-process-token"},
            json={},
        )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["endpoint"]["bearer_ref"] == "env://GRAPHOS_CONTEXT_TOKEN"
    assert result["proof"]["endpoint_url"] == result["endpoint"]["url"]
    assert "secret-value-never-exported" not in response.text
    assert calls == [
        "probe:env://GRAPHOS_CONTEXT_TOKEN",
        "resolve:env://GRAPHOS_CONTEXT_TOKEN",
    ]


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


def test_runtime_assembler_binds_public_eg_audit_and_one_fleet_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.api.ops.harness import specs
    from graph_os.fleet.gateway_ops import FleetGateway

    monkeypatch.setattr(serving, "_SERVED_PORTS", None)
    monkeypatch.setattr(serving, "get_registry", lambda: Registry(specs()))
    basic = ports([])

    gateway = FleetGateway(
        tool_for=lambda *args: None,
        policy_check=lambda *args: None,
        delegated_call=lambda *args: None,
    )
    audit = SimpleNamespace(preflight=basic.audit_preflight, write=basic.audit_write)
    monkeypatch.setattr(serving, "bind_eg_audit", lambda client: audit)

    async def resolve(reference: str) -> str:
        return "verified-secret"

    def authorities(**changes: Any) -> serving.RuntimeAuthorities:
        fields = dict(
            caller_client=basic.caller_client,
            service_client=basic.service_client,
            service_claims=basic.service_claims,
            check_access=basic.check_access,
            service_scopes=basic.service_scopes,
            plan_client=basic.plan_client,
            plan_seal_key=basic.plan_seal_key,
            policy_gate=basic.policy_gate,
            fleet_gateway=gateway,
            fleet_search=lambda *args: (),
            fleet_ops_factory=lambda *args: None,
            resolver=IntentResolver(),
            bearer_ref="env://GRAPHOS_CONTEXT_TOKEN",
            resolve_bearer=resolve,
            bindings={},
        )
        fields.update(changes)
        return serving.RuntimeAuthorities(**fields)

    with pytest.raises(ValueError, match="governed fleet gateway"):
        serving.configure_runtime_authorities(authorities(fleet_gateway=object()))
    with pytest.raises(ValueError, match="action verifier binding"):
        serving.configure_runtime_authorities(
            authorities(bindings={"action_verify": object()})
        )
    from graph_os.access.approvals import ApprovalService

    with pytest.raises(ValueError, match="approval decision authority"):
        serving.configure_runtime_authorities(
            authorities(
                bindings={"approvals": ApprovalService(decision_authority=object())}
            )
        )
    with pytest.raises(ValueError, match="bearer reference"):
        serving.configure_runtime_authorities(authorities(bearer_ref="raw-token"))
    with pytest.raises(ValueError, match="policy_gate"):
        serving.configure_runtime_authorities(authorities(policy_gate=None))
    assert serving._SERVED_PORTS is None

    serving.configure_runtime_authorities(authorities())
    bound = serving.configured_served_api_ports()
    services = serving.build_invoke_services(bound.serving)
    assert bound.serving.eg_dispatch is serving.dispatch_public_eg_method
    assert bound.serving.schema_validate is serving.validate_public_eg_params
    assert bound.serving.audit_preflight is audit.preflight
    assert bound.serving.audit_write is audit.write
    assert bound.serving.fleet_effect == gateway.effect
    assert services.runtime.bindings["fleet_gateway"] is gateway
    assert callable(bound.serving.context_endpoint_export)


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
    assert caller.request_id
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

    session = SimpleNamespace(
        tenant="t1",
        graph="tenant-a/research",
        actor=SimpleNamespace(actor_id="user:1"),
        ensure_authority_current=lambda: None,
    )
    context = SimpleNamespace(
        client=PublicClient(),
        caller=SimpleNamespace(tenant="t1", principal="user:1", session=session),
        idempotency_key="request:1",
    )
    result = await serving.dispatch_public_eg_method(
        EgMethod(service="Items", op="GetItem"), {"subject": "item:1"}, context
    )
    assert result == {"subject": "item:1"}
    assert seen == [
        ("GetItem", {"subject": "item:1"}),
        (
            "GetItem",
            {"graph": "tenant-a/research", "idempotency_key": "request:1"},
        ),
    ]


@pytest.mark.asyncio
async def test_public_eg_dispatch_uses_verified_graph_and_preserves_engine_denial() -> (
    None
):
    class GrantCheckingClient:
        async def invoke_method(self, method: str, params: Any, **kwargs: Any) -> Any:
            if kwargs["graph"] not in {None, "tenant-a/allowed"}:
                raise PermissionError("graph grant denied")
            return kwargs["graph"]

    session = SimpleNamespace(
        tenant="tenant-a",
        graph="tenant-a/allowed",
        actor=SimpleNamespace(actor_id="user:1"),
        ensure_authority_current=lambda: None,
    )
    caller = SimpleNamespace(tenant="tenant-a", principal="user:1", session=session)
    context = SimpleNamespace(
        client=GrantCheckingClient(), caller=caller, idempotency_key=None
    )
    binding = EgMethod(service="Items", op="GetItem")
    assert (
        await serving.dispatch_public_eg_method(binding, {}, context)
        == "tenant-a/allowed"
    )
    session.graph = "tenant-a/denied"
    with pytest.raises(PermissionError, match="graph grant denied"):
        await serving.dispatch_public_eg_method(binding, {}, context)
    session.graph = ""
    assert await serving.dispatch_public_eg_method(binding, {}, context) is None
    caller.session = None
    with pytest.raises(PermissionError, match="verified graph session"):
        await serving.dispatch_public_eg_method(binding, {}, context)
    caller.session = session
    caller.tenant = "other-tenant"
    with pytest.raises(PermissionError, match="authority differ"):
        await serving.dispatch_public_eg_method(binding, {}, context)
