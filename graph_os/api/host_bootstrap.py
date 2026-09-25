"""Adapt verified GraphOS process facilities to the served API authority set."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.host_secrets import host_secret_ports_from_config
from graph_os.api.invoke.executor import ServiceClaims, SubjectCheck
from graph_os.api.mcp.discovery import FleetSearch
from graph_os.api.mcp.resolve import IntentResolver
from graph_os.api.policy import PolicyGate
from graph_os.api.serving import RuntimeAuthorities, configure_runtime_authorities


@dataclass(frozen=True, slots=True)
class HostRuntimeInputs:
    """Authorities that the current GraphOS process cannot infer safely.

    The identity owner must provide verified SERVICE claims, EG CheckAccess,
    and a tenant-safe control-lease client. Fleet ownership supplies one
    governed gateway and its matching search/ops factory. Their absence stops
    assembly; there is no anonymous process or static-catalog fallback.
    """

    identity_mode: str
    service_claims: ServiceClaims
    check_access: SubjectCheck
    service_scopes: frozenset[str]
    plan_client: Any
    fleet_gateway: Any
    fleet_search: FleetSearch
    fleet_ops_factory: Any
    resolver: IntentResolver
    bindings: Mapping[str, Any]


def host_runtime_authorities(inputs: HostRuntimeInputs) -> RuntimeAuthorities:
    """Use the process EG route, configured Eunomia and required secret refs."""

    if not isinstance(inputs, HostRuntimeInputs):
        raise TypeError("complete host runtime inputs are required")
    from agent_utilities.core.config import config, setting

    from graph_os.mcp_server import runtime

    secrets = host_secret_ports_from_config()
    policy = PolicyGate.from_config(
        config, inputs.identity_mode, configured_mode=setting("EUNOMIA_TYPE")
    )

    async def graph_client(tenant: str) -> Any:
        if not isinstance(tenant, str) or not tenant:
            raise ValueError("verified tenant graph is required")
        client = runtime.graph_client(tenant)
        if not callable(getattr(client, "use_verified_context", None)):
            raise RuntimeError("verified EG client is unavailable")
        return client

    return RuntimeAuthorities(
        caller_client=graph_client,
        service_client=graph_client,
        service_claims=inputs.service_claims,
        check_access=inputs.check_access,
        service_scopes=inputs.service_scopes,
        plan_client=inputs.plan_client,
        plan_seal_key=secrets.plan_seal_key,
        policy_gate=policy,
        fleet_gateway=inputs.fleet_gateway,
        fleet_search=inputs.fleet_search,
        fleet_ops_factory=inputs.fleet_ops_factory,
        resolver=inputs.resolver,
        bearer_ref=secrets.context_bearer_ref,
        resolve_bearer=secrets.resolve_bearer,
        bindings=inputs.bindings,
    )


def configure_host_runtime(inputs: HostRuntimeInputs) -> None:
    """Install one verified host bundle before ``mcp_server`` starts serving."""

    configure_runtime_authorities(host_runtime_authorities(inputs))
