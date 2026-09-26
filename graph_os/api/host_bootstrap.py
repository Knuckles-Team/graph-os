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
from graph_os.fleet.catalog_sources import SdkRead


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
    policy_gate: PolicyGate | None = None
    fleet_reader: Any = None


class _ProcessControlLeases:
    """Bind every plan lease call to the verified process's own tenant."""

    def __init__(self, client: Any, session: Any) -> None:
        self._client = client
        self._session = session

    async def _call(self, method: str, **kwargs: Any) -> Any:
        claims = self._session.engine_verified_context()
        if claims.get("principal") != "svc:graph-os" or kwargs.get(
            "tenant"
        ) != claims.get("tenant"):
            raise PermissionError("plan lease service authority is unavailable")
        leases = getattr(self._client, "control_leases", None)
        operation = getattr(leases, method, None)
        if not callable(operation):
            raise RuntimeError("EG control lease contract is unavailable")
        with self._client.use_verified_context(claims):
            return await operation(**kwargs)

    async def issue(self, **kwargs: Any) -> Any:
        return await self._call("issue", **kwargs)

    async def get(self, **kwargs: Any) -> Any:
        return await self._call("get", **kwargs)

    async def transition(self, **kwargs: Any) -> Any:
        return await self._call("transition", **kwargs)


class _ProcessPlanClient:
    def __init__(self, client: Any, session: Any) -> None:
        self.control_leases = _ProcessControlLeases(client, session)


def verified_process_inputs(
    session: Any,
    *,
    identity_mode: str,
    fleet_gateway: Any,
    fleet_search: FleetSearch,
    fleet_ops_factory: Any,
    resolver: IntentResolver,
    bindings: Mapping[str, Any],
    policy_gate: PolicyGate | None = None,
    fleet_reader: Any = None,
) -> HostRuntimeInputs:
    """Derive service and EG ports only from a current process credential.

    The process token authorizes its own tenant only. A multi-tenant host must
    supply a separate verified per-tenant service credential provider instead
    of rewriting this token's claims. Fleet ports remain explicit because the
    EG catalog, child gateway, and PDP must come from the same serving host.
    """

    from graph_os.mcp_server import runtime

    claims = session.engine_verified_context()
    if claims.get("principal") != "svc:graph-os" or not claims.get("tenant"):
        raise PermissionError("verified graph-os service identity is required")
    scopes = claims.get("scopes")
    if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
        raise PermissionError("verified graph-os service scopes are required")
    client = runtime.graph_client(str(claims["tenant"]))
    if not callable(getattr(client, "use_verified_context", None)):
        raise RuntimeError("verified EG client is unavailable")

    def service_claims(tenant: str) -> Mapping[str, Any]:
        current = session.engine_verified_context()
        if (
            current.get("principal") != "svc:graph-os"
            or current.get("tenant") != tenant
        ):
            raise PermissionError("service credential has no authority for tenant")
        return current

    async def check_access(eg_client: Any, caller: Any, subject: str) -> bool:
        if (
            caller.session is None
            or caller.session.tenant != caller.tenant
            or caller.session.actor.actor_id != caller.principal
            or not isinstance(subject, str)
            or not subject
        ):
            raise PermissionError("verified caller subject is required")
        checker = getattr(eg_client, "check_access", None)
        if not callable(checker):
            raise RuntimeError("EG CheckAccess contract is unavailable")
        return await checker(caller.principal, "read", graph=subject)

    return HostRuntimeInputs(
        identity_mode=identity_mode,
        service_claims=service_claims,
        check_access=check_access,
        service_scopes=frozenset(scopes),
        plan_client=_ProcessPlanClient(client, session),
        fleet_gateway=fleet_gateway,
        fleet_search=fleet_search,
        fleet_ops_factory=fleet_ops_factory,
        resolver=resolver,
        bindings=bindings,
        policy_gate=policy_gate,
        fleet_reader=fleet_reader,
    )


def compose_process_host_inputs(
    session: Any,
    *,
    identity_mode: str,
    fleet_reader: Any,
    sdk_entries: SdkRead,
    bindings: Mapping[str, Any],
) -> HostRuntimeInputs:
    """Use one configured PDP for the registry and the attached fleet.

    The caller supplies real EG and SDK catalog ports. The returned input
    bundle may be passed to ``mcp_server(host_runtime_inputs=...)``; the CLI
    cannot infer or fabricate the fleet reader, pack reader, or service grant.
    """

    from agent_utilities.core.config import config, setting

    from graph_os.api.fleet_host import compose_fleet_host_ports
    from graph_os.fleet.catalog_reader import DeferredFleetCatalogReader

    if not isinstance(fleet_reader, DeferredFleetCatalogReader):
        raise ValueError("composed host needs the process fleet reader")

    policy = PolicyGate.from_config(
        config, identity_mode, configured_mode=setting("EUNOMIA_TYPE")
    )
    fleet = compose_fleet_host_ports(
        reader=fleet_reader, sdk_entries=sdk_entries, policy_gate=policy
    )
    return verified_process_inputs(
        session,
        identity_mode=identity_mode,
        fleet_gateway=fleet.gateway,
        fleet_search=fleet.search,
        fleet_ops_factory=fleet.ops_factory,
        resolver=fleet.resolver,
        bindings=bindings,
        policy_gate=policy,
        fleet_reader=fleet_reader,
    )


def host_runtime_authorities(inputs: HostRuntimeInputs) -> RuntimeAuthorities:
    """Use the process EG route, configured Eunomia and required secret refs."""

    if not isinstance(inputs, HostRuntimeInputs):
        raise TypeError("complete host runtime inputs are required")
    from agent_utilities.core.config import config, setting

    from graph_os.mcp_server import runtime

    secrets = host_secret_ports_from_config()
    policy = inputs.policy_gate or PolicyGate.from_config(
        config, inputs.identity_mode, configured_mode=setting("EUNOMIA_TYPE")
    )
    if not isinstance(policy, PolicyGate):
        raise ValueError("verified host policy gate is unavailable")

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
