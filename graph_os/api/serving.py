"""Assemble the one operation registry and its caller-bound execution ports.

The serving process owns these ports. Surface adapters receive the returned
InvokeServices and must never construct an alternate registry or executor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.invoke.executor import (
    BoundOperationRuntime,
    ClientFactory,
    EgDispatch,
    SubjectCheck,
)
from graph_os.api.invoke.pipeline import (
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
from graph_os.api.registry import Executor, Registry


@dataclass(frozen=True, slots=True)
class ServingPorts:
    """Authorities supplied by the verified GraphOS serving lifecycle."""

    caller_client: ClientFactory
    service_client: ClientFactory
    check_access: SubjectCheck
    eg_dispatch: EgDispatch
    service_scopes: frozenset[str]
    plan_client: Any
    policy_gate: PolicyGate
    audit_write: AuditWrite
    schema_validate: SchemaValidate
    fleet_effect: FleetEffect
    bindings: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ServedApiPorts:
    """One explicitly registered authority bundle for all served surfaces."""

    serving: ServingPorts
    caller_for_request: Callable[[], Any]
    fleet_search: FleetSearch
    fleet_ops_factory: Callable[..., Any]
    resolver: IntentResolver


_SERVED_PORTS: ServedApiPorts | None = None


def configure_served_api_ports(ports: ServedApiPorts) -> None:
    """Register one process-owned bundle before server construction."""

    if not isinstance(ports, ServedApiPorts):
        raise TypeError("complete served API ports are required")
    global _SERVED_PORTS
    if _SERVED_PORTS is not None:
        raise RuntimeError("served API ports already configured")
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
        return VerifiedCaller.from_session(session)


def _require_callable(value: Any, name: str) -> None:
    if not callable(value):
        raise ValueError(f"serving port {name} is unavailable")


def _validate_ports(ports: ServingPorts, registry: Registry) -> None:
    for name in (
        "caller_client",
        "service_client",
        "check_access",
        "eg_dispatch",
        "audit_write",
        "schema_validate",
        "fleet_effect",
    ):
        _require_callable(getattr(ports, name), name)
    if not isinstance(ports.policy_gate, PolicyGate):
        raise ValueError("serving port policy_gate is unavailable")
    if not isinstance(ports.bindings, Mapping):
        raise ValueError("serving port bindings is unavailable")
    if not isinstance(ports.service_scopes, frozenset):
        raise ValueError("serving port service_scopes is unavailable")
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
    runtime = BoundOperationRuntime(
        caller_client=ports.caller_client,
        service_client=ports.service_client,
        check_access=ports.check_access,
        eg_dispatch=ports.eg_dispatch,
        service_scopes=ports.service_scopes,
        bindings=ports.bindings,
    )
    return InvokeServices(
        registry=registry,
        runtime=runtime,
        plans=EgPlanStore(ports.plan_client),
        policy_mode="off" if ports.policy_gate.mode == "none" else "on",
        policy_check=ports.policy_gate.check_op,
        audit_write=ports.audit_write,
        fleet_effect=ports.fleet_effect,
        schema_validate=ports.schema_validate,
    )


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
