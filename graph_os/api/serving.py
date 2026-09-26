"""Assemble the one operation registry and its caller-bound execution ports.

The serving process owns these ports. Surface adapters receive the returned
InvokeServices and must never construct an alternate registry or executor.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent_connector_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)

from graph_os.api.harness_context import (
    BearerResolver,
    configured_eg_context_endpoint,
)
from graph_os.api.invoke.eg_audit import EgAuditAdapter
from graph_os.api.invoke.executor import (
    BoundOperationRuntime,
    ClientFactory,
    EgDispatch,
    ServiceClaims,
    SubjectCheck,
)
from graph_os.api.invoke.pipeline import (
    AuditPreflight,
    AuditWrite,
    FleetEffect,
    InvokeServices,
)
from graph_os.api.invoke.plan import EgPlanStore
from graph_os.api.invoke.steps import SchemaValidate, VerifiedCaller
from graph_os.api.mcp.discovery import FleetSearch
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.mcp.verbs import MCPProjection
from graph_os.api.ops import get_registry
from graph_os.api.policy import PolicyGate, op_resource
from graph_os.api.registry import EgMethod, EgSchemaRef, Executor, Registry


@dataclass(frozen=True, slots=True)
class ServingPorts:
    """Authorities supplied by the verified GraphOS serving lifecycle."""

    caller_client: ClientFactory
    service_client: ClientFactory
    service_claims: ServiceClaims
    check_access: SubjectCheck
    eg_dispatch: EgDispatch
    service_scopes: frozenset[str]
    plan_client: Any
    plan_seal_key: bytes
    policy_gate: PolicyGate
    audit_preflight: AuditPreflight
    audit_write: AuditWrite
    schema_validate: SchemaValidate
    fleet_effect: FleetEffect
    bindings: Mapping[str, Any]
    context_endpoint_export: Callable[[], Awaitable[tuple[Any, Any]]] | None = None


@dataclass(frozen=True, slots=True)
class ServedApiPorts:
    """One explicitly registered authority bundle for all served surfaces."""

    serving: ServingPorts
    caller_for_request: Callable[[], Any]
    fleet_search: FleetSearch
    fleet_ops_factory: Callable[..., Any]
    resolver: IntentResolver


@dataclass(frozen=True, slots=True)
class RuntimeAuthorities:
    """Process-owned authorities needed to construct every served API port.

    The host supplies verified identity, policy, fleet and EG clients. The
    assembler below binds the public EG contract and its durable audit client;
    it never manufactures grants or replaces a missing authority.
    """

    caller_client: ClientFactory
    service_client: ClientFactory
    service_claims: ServiceClaims
    check_access: SubjectCheck
    service_scopes: frozenset[str]
    plan_client: Any
    plan_seal_key: bytes
    policy_gate: PolicyGate
    fleet_gateway: Any
    fleet_search: FleetSearch
    fleet_ops_factory: Callable[..., Any]
    resolver: IntentResolver
    bearer_ref: str
    resolve_bearer: BearerResolver
    bindings: Mapping[str, Any]


_SERVED_PORTS: ServedApiPorts | None = None


def assemble_runtime_authorities(authorities: RuntimeAuthorities) -> ServedApiPorts:
    """Construct one complete served bundle from explicit process authorities.

    EG schema validation and dispatch use its packaged public contract. Audit
    preflight and outcome append use the caller-bound durable EG adapter.
    Invalid or incomplete input fails before the process registers any ports.
    """

    if not isinstance(authorities, RuntimeAuthorities):
        raise TypeError("complete runtime authorities are required")
    if not isinstance(authorities.bindings, Mapping):
        raise ValueError("runtime bindings are unavailable")
    if "fleet_gateway" in authorities.bindings:
        raise ValueError("fleet_gateway is reserved for the serving root")
    from graph_os.access.action_verify import ActionVerifier
    from graph_os.access.approvals import ApprovalService

    approval_service = authorities.bindings.get("approvals")
    if approval_service is None:
        approval_service = ApprovalService()
    elif not isinstance(approval_service, ApprovalService):
        raise ValueError("approval service binding is invalid")
    elif not approval_service.serving_safe:
        raise ValueError("approval decision authority is not native")
    action_verifier = authorities.bindings.get("action_verify")
    if action_verifier is not None and not isinstance(action_verifier, ActionVerifier):
        raise ValueError("action verifier binding is invalid")
    from graph_os.fleet.gateway_ops import FleetGateway

    if not isinstance(authorities.fleet_gateway, FleetGateway):
        raise ValueError("governed fleet gateway is unavailable")
    audit = bind_eg_audit(authorities.caller_client)
    serving = bind_context_endpoint_export(
        ServingPorts(
            caller_client=authorities.caller_client,
            service_client=authorities.service_client,
            service_claims=authorities.service_claims,
            check_access=authorities.check_access,
            eg_dispatch=dispatch_public_eg_method,
            service_scopes=authorities.service_scopes,
            plan_client=authorities.plan_client,
            plan_seal_key=authorities.plan_seal_key,
            policy_gate=authorities.policy_gate,
            audit_preflight=audit.preflight,
            audit_write=audit.write,
            schema_validate=validate_public_eg_params,
            fleet_effect=authorities.fleet_gateway.effect,
            bindings={
                **authorities.bindings,
                "approvals": approval_service,
                "fleet_gateway": authorities.fleet_gateway,
            },
        ),
        bearer_ref=authorities.bearer_ref,
        resolve_bearer=authorities.resolve_bearer,
    )
    ports = ServedApiPorts(
        serving=serving,
        caller_for_request=caller_from_verified_session,
        fleet_search=authorities.fleet_search,
        fleet_ops_factory=authorities.fleet_ops_factory,
        resolver=authorities.resolver,
    )
    assemble_served_api(ports)
    return ports


def configure_runtime_authorities(authorities: RuntimeAuthorities) -> None:
    """Validate and register the production bundle before server startup."""

    ports = assemble_runtime_authorities(authorities)
    configure_served_api_ports(ports)


def bind_context_endpoint_export(
    ports: ServingPorts, *, bearer_ref: str, resolve_bearer: BearerResolver
) -> ServingPorts:
    """Bind the harness export to an explicit secret reference and resolver.

    Resolution and the live authorized MCP probe happen for each invocation.
    The exported descriptor contains the reference, never the bearer value.
    """

    if ports.context_endpoint_export is not None:
        raise ValueError("context endpoint export is already bound")
    try:
        reference = parse_secret_reference(bearer_ref).render()
    except SecretReferenceError as exc:
        raise ValueError("context endpoint bearer reference is unavailable") from exc
    _require_callable(resolve_bearer, "context_endpoint_resolver")

    async def export() -> tuple[Any, Any]:
        return await configured_eg_context_endpoint(
            bearer_ref=reference, resolve_bearer=resolve_bearer
        )

    return replace(ports, context_endpoint_export=export)


def configure_served_api_ports(
    ports: ServedApiPorts,
    *,
    context_bearer_ref: str | None = None,
    resolve_bearer: BearerResolver | None = None,
) -> None:
    """Register one process-owned bundle before server construction.

    The host may supply an explicit export port, or these two bootstrap inputs
    to bind the configured EG MCP probe. A partial pair never serves.
    """

    if not isinstance(ports, ServedApiPorts):
        raise TypeError("complete served API ports are required")
    global _SERVED_PORTS
    if _SERVED_PORTS is not None:
        raise RuntimeError("served API ports already configured")
    if (context_bearer_ref is None) != (resolve_bearer is None):
        raise ValueError("context endpoint reference and resolver are both required")
    if context_bearer_ref is not None and resolve_bearer is not None:
        ports = replace(
            ports,
            serving=bind_context_endpoint_export(
                ports.serving,
                bearer_ref=context_bearer_ref,
                resolve_bearer=resolve_bearer,
            ),
        )
    _SERVED_PORTS = ports


def configured_served_api_ports() -> ServedApiPorts:
    """Never infer authority from ambient globals or anonymous defaults."""

    if _SERVED_PORTS is None:
        raise RuntimeError("served API authority ports are not configured")
    return _SERVED_PORTS


def caller_from_verified_session() -> VerifiedCaller:
    """Snapshot the middleware or stdio process session, never request hints."""

    from graph_os.mcp_server.runtime import verified_tool_session_scope

    with verified_tool_session_scope() as session:
        return VerifiedCaller.from_session(session, request_id=str(uuid.uuid4()))


_EG_REQUEST_REF = re.compile(
    r"^contract/schemas/method\.request\.json#/methods/([A-Za-z][A-Za-z0-9_]*)$"
)


def validate_public_eg_params(
    reference: EgSchemaRef, params: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Validate an EG method's params through its packaged generated contract."""

    match = _EG_REQUEST_REF.fullmatch(reference.path)
    if match is None:
        raise ValueError("EG request schema reference is unavailable")
    from epistemic_graph.contract.invocation import validate_method_params

    return validate_method_params(match.group(1), dict(params))


async def dispatch_public_eg_method(
    binding: EgMethod, params: Mapping[str, Any], context: Any
) -> Any:
    """Call EG under the verified session's graph, not its auth tenant.

    An empty session graph selects the client's configured default. The EG
    server checks the caller's graph grant for either target; a request param
    cannot retarget the transport independently of the verified session.
    """

    if not isinstance(binding, EgMethod):
        raise ValueError("EG method binding required")
    caller = context.caller
    session = caller.session
    if session is None:
        raise PermissionError("verified graph session required")
    session.ensure_authority_current()
    graph = session.graph
    if (
        not isinstance(graph, str)
        or session.tenant != caller.tenant
        or session.actor.actor_id != caller.principal
    ):
        raise PermissionError("graph target and verified caller authority differ")
    method = getattr(context.client, "invoke_method", None)
    if not callable(method):
        raise RuntimeError("public EG method invoker is unavailable")
    return await method(
        binding.op,
        dict(params),
        graph=graph or None,
        idempotency_key=context.idempotency_key,
    )


def _require_callable(value: Any, name: str) -> None:
    if not callable(value):
        raise ValueError(f"serving port {name} is unavailable")


def bind_eg_audit(client_factory: ClientFactory) -> EgAuditAdapter:
    """Create the durable audit authority after checking the installed EG API."""

    _require_callable(client_factory, "audit_client")
    EgAuditAdapter.require_contract()
    return EgAuditAdapter(client_factory)


def _validate_ports(ports: ServingPorts, registry: Registry) -> None:
    for name in (
        "caller_client",
        "service_client",
        "service_claims",
        "check_access",
        "eg_dispatch",
        "audit_preflight",
        "audit_write",
        "schema_validate",
        "fleet_effect",
    ):
        _require_callable(getattr(ports, name), name)
    if not isinstance(ports.policy_gate, PolicyGate):
        raise ValueError("serving port policy_gate is unavailable")
    if not isinstance(ports.bindings, Mapping):
        raise ValueError("serving port bindings is unavailable")
    if registry.get("harness.context_endpoint") is not None:
        _require_callable(ports.context_endpoint_export, "context_endpoint_export")
    if not isinstance(ports.service_scopes, frozenset):
        raise ValueError("serving port service_scopes is unavailable")
    if not isinstance(ports.plan_seal_key, bytes) or len(ports.plan_seal_key) != 32:
        raise ValueError("serving port plan_seal_key is unavailable")
    leases = getattr(ports.plan_client, "control_leases", None)
    if leases is None or not all(
        callable(getattr(leases, method, None))
        for method in ("issue", "get", "transition")
    ):
        raise ValueError("serving port plan_client lacks EG control leases")
    for op in registry:
        if op.executor is Executor.SERVICE and not op.executor_scopes.issubset(
            ports.service_scopes
        ):
            raise ValueError(f"service grants unavailable for {op.id}")


def build_invoke_services(ports: ServingPorts) -> InvokeServices:
    """Build the strict curated and EG registry from the pinned wheel contract.

    Missing domain declarations, wheel schemas, policy or executor ports abort
    assembly. No partial registry or permissive authority fallback is served.
    """

    registry = get_registry()
    if not isinstance(registry, Registry) or not len(registry):
        raise ValueError("operation registry is unavailable")
    _validate_ports(ports, registry)
    bindings = dict(ports.bindings)
    if "invoke_services" in bindings:
        raise ValueError("invoke_services is reserved for the serving root")
    if "context_endpoint_export" in bindings:
        raise ValueError("context_endpoint_export is reserved for the serving root")
    if ports.context_endpoint_export is not None:
        bindings["context_endpoint_export"] = ports.context_endpoint_export
    bindings["invoke_services"] = None
    runtime = BoundOperationRuntime(
        caller_client=ports.caller_client,
        service_client=ports.service_client,
        service_claims=ports.service_claims,
        check_access=ports.check_access,
        eg_dispatch=ports.eg_dispatch,
        service_scopes=ports.service_scopes,
        bindings=bindings,
    )
    services = InvokeServices(
        registry=registry,
        runtime=runtime,
        plans=EgPlanStore(ports.plan_client, seal_key=ports.plan_seal_key),
        policy_mode="off" if ports.policy_gate.mode == "none" else "on",
        policy_check=ports.policy_gate.check_op,
        audit_preflight=ports.audit_preflight,
        audit_write=ports.audit_write,
        fleet_effect=ports.fleet_effect,
        schema_validate=ports.schema_validate,
    )
    bindings["invoke_services"] = services
    return services


Visibility = Callable[[Any, Any], Awaitable[bool]]


def assemble_served_api(
    ports: ServedApiPorts,
) -> tuple[MCPProjection, Visibility, Callable[..., Any]]:
    """Share one registry, invoke bundle and policy gate across MCP and HTTP."""

    if not isinstance(ports, ServedApiPorts):
        raise TypeError("complete served API ports are required")
    for name in ("caller_for_request", "fleet_search", "fleet_ops_factory"):
        _require_callable(getattr(ports, name), name)
    if not isinstance(ports.resolver, IntentResolver):
        raise ValueError("serving port resolver is unavailable")
    services = build_invoke_services(ports.serving)
    policy_gate = ports.serving.policy_gate

    async def visibility(op: Any, caller: Any) -> bool:
        return (await policy_gate.visible([op_resource(op)], caller))[0]

    projection = MCPProjection(
        registry=services.registry,
        services=services,
        resolver=ports.resolver,
        caller_for_request=ports.caller_for_request,
        policy_gate=policy_gate,
        fleet_search=ports.fleet_search,
    )
    return projection, visibility, ports.fleet_ops_factory
