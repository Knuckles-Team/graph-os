"""Assemble the one operation registry and its caller-bound execution ports.

The serving process owns these ports. Surface adapters receive the returned
InvokeServices and must never construct an alternate registry or executor.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from graph_os.api.invoke.steps import SchemaValidate
from graph_os.api.ops import get_registry
from graph_os.api.policy import PolicyGate
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
