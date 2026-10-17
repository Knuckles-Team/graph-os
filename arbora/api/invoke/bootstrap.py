"""Production ``InvokeServices``/``BoundOperationRuntime`` composition.

Assemble the ARBORA-OPS-R009 operation runtime from Arbora's existing
epistemic-graph adapter, audit adapter and plan store. The serving process
must separately compose and admit these authorities before mounting them.

Generated caller-bound EG methods use the provider's public ``send_by_id``
entrypoint. The verified session supplies the graph; operation parameters
cannot select the dispatch context. Service-executor operations still require
an explicitly composed subject-access authority; the default denies access.
These adapters do not perform the serving-process cutover or supply missing
policy, audit, plan, or request-identity authorities.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from arbora.api.invoke.eg_audit import EgAuditAdapter
from arbora.api.invoke.executor import (
    BoundOperationRuntime,
    ClientFactory,
    ExecutionContext,
    ServiceClaims,
    SubjectCheck,
)
from arbora.api.invoke.pipeline import InvokeServices, OperationRefused
from arbora.api.invoke.plan import EgPlanStore
from arbora.api.invoke.steps import (
    VerifiedCaller,
    authenticate,
    claim_values,
    freeze_claims,
)
from arbora.api.policy.eunomia import PolicyGate
from arbora.api.registry import EgMethod, Registry
from arbora.epistemic import ClientContext, EpistemicClientPool, VerifiedActor

__all__ = [
    "SessionRequiredError",
    "VerifiedSession",
    "build_client_factory",
    "build_invoke_services",
    "build_operation_runtime",
    "build_service_claims",
    "deny_subject_access",
    "dispatch_eg_method",
    "verify_current_from_session",
]


class SessionRequiredError(RuntimeError):
    """``verify_current_from_session`` found no current, matching session."""


class VerifiedSession(Protocol):
    """The ambient session shape :func:`verify_current_from_session` needs."""

    actor: VerifiedActor
    tenant: str

    def engine_verified_context(self) -> Mapping[str, Any]: ...


def build_client_factory(
    pool: EpistemicClientPool,
    *,
    graph: str,
    connect_context: Callable[[str], ClientContext],
) -> ClientFactory:
    """A ``ClientFactory`` over one pool-owned graph, by requested tenant.

    ``connect_context`` builds the :class:`~arbora.epistemic.ClientContext`
    used only for the pool's first connection to ``graph`` under a given
    tenant -- Arbora's own verified process identity, never the per-request
    caller. The per-request caller authority is applied afterward, by
    ``BoundOperationRuntime.as_caller``/``as_service``, through the returned
    client's own ``use_verified_context``.
    """

    async def _client(tenant: str) -> Any:
        return await pool.connected_client(connect_context(tenant), graph)

    return _client


async def verify_current_from_session(caller: VerifiedCaller) -> VerifiedCaller:
    """Reconfirm the caller's session is current; refresh its engine claims.

    Mirrors ``arbora.mcp_server.runtime.verified_tool_session_scope``'s own
    actor-consistency check, so the invocation pipeline enforces the same
    authority the MCP/REST transport already bound at request admission --
    a renewed, revoked, or mismatched session is caught here, never assumed
    from the surface adapter's own claims.
    """

    session = caller.session
    if session is None:
        raise SessionRequiredError("invocation caller carries no verified session")
    actor = session.actor
    if actor.actor_id != caller.principal or session.tenant != caller.tenant:
        raise SessionRequiredError("verified session no longer matches this caller")
    claims = session.engine_verified_context()
    return replace(caller, engine_claims=freeze_claims(claims))


def build_service_claims(
    process_actor: Callable[[], VerifiedActor],
    *,
    audience: str,
    policy_version: str,
) -> ServiceClaims:
    """Mint exact-scope service claims from Arbora's own verified process actor.

    Reuses :meth:`~arbora.epistemic.ClientContext.from_actor`, which
    refuses a stale credential and rejects a tenant that does not match the
    verified actor -- this never mints authority the process does not
    already hold. ``process_actor`` is read fresh on every call so a renewed
    process credential is picked up without rebuilding the runtime.
    """

    async def _claims(
        tenant: str, required_scopes: frozenset[str]
    ) -> Mapping[str, Any]:
        context = ClientContext.from_actor(
            process_actor(),
            tenant=tenant,
            audience=audience,
            scopes=required_scopes,
            policy_version=policy_version,
        )
        return context.to_claims()

    return _claims


async def deny_subject_access(
    _client: Any, _caller: VerifiedCaller, _subject: str
) -> bool:
    """Deny service execution until a subject-access authority is composed."""

    return False


def _eg_method_id(binding: EgMethod) -> str:
    from epistemic_graph.generated import METHOD_IDS

    if (
        not isinstance(binding, EgMethod)
        or binding.service != binding.op
        or binding.service not in METHOD_IDS
    ):
        raise OperationRefused("UNAVAILABLE", {"reason": "invalid engine binding"})
    return binding.service


async def _caller_dispatch_graph(context: ExecutionContext) -> str:
    caller = context.caller
    if context.service_identity:
        raise OperationRefused("PRINCIPAL_NOT_ALLOWED")
    if authenticate(caller) is not None or context.owner != caller.principal:
        raise OperationRefused("UNAUTHENTICATED")
    try:
        current = await verify_current_from_session(caller)
        actor = caller.session.actor
        actor.ensure_credential_current()
    except (
        AttributeError,
        PermissionError,
        SessionRequiredError,
        TypeError,
        ValueError,
    ):
        raise OperationRefused("UNAUTHENTICATED") from None
    if (
        current != caller
        or actor.authenticated is not True
        or actor.tenant_id != caller.tenant
    ):
        raise OperationRefused("UNAUTHENTICATED")
    return _session_graph(caller.session)


def _session_graph(session: Any) -> str:
    graph = getattr(session, "graph", None)
    if not isinstance(graph, str) or not graph or graph != graph.strip():
        raise OperationRefused("UNAUTHENTICATED")
    return graph


async def dispatch_eg_method(
    binding: EgMethod, params: Mapping[str, Any], context: ExecutionContext
) -> Any:
    """Send one admitted method under its current caller's verified graph."""
    from epistemic_graph.generated import send_by_id

    method = _eg_method_id(binding)
    graph = await _caller_dispatch_graph(context)
    payload = claim_values(params)
    if method == "NlQuery":
        if "graph" in payload:
            raise OperationRefused("INVALID_ARGUMENT", {"field": "graph"})
        payload["graph"] = graph
    return await send_by_id(
        context.client,
        method,
        payload,
        graph,
        idempotency_key=context.idempotency_key,
    )


def build_operation_runtime(
    *,
    pool: EpistemicClientPool,
    graph: str,
    connect_context: Callable[[str], ClientContext],
    service_scopes: frozenset[str],
    service_principal: str,
    service_claims: ServiceClaims,
    check_access: SubjectCheck = deny_subject_access,
    eg_dispatch: Callable[[Any, Any, Any], Any] = dispatch_eg_method,
    bindings: Mapping[str, Any] | None = None,
) -> BoundOperationRuntime:
    """Assemble the real caller/service client factories and EG dispatch port."""

    client_factory = build_client_factory(
        pool, graph=graph, connect_context=connect_context
    )

    async def verify_bound_caller(caller: VerifiedCaller) -> VerifiedCaller:
        current = await verify_current_from_session(caller)
        if _session_graph(current.session) != graph:
            raise SessionRequiredError("caller graph differs from the runtime graph")
        return current

    async def dispatch_bound_method(
        binding: Any, params: Mapping[str, Any], context: ExecutionContext
    ) -> Any:
        if _session_graph(context.caller.session) != graph:
            raise OperationRefused("UNAUTHENTICATED")
        return await eg_dispatch(binding, params, context)

    return BoundOperationRuntime(
        caller_client=client_factory,
        verify_current=verify_bound_caller,
        service_client=client_factory,
        service_claims=service_claims,
        check_access=check_access,
        eg_dispatch=dispatch_bound_method,
        service_scopes=service_scopes,
        service_principal=service_principal,
        bindings=bindings,
    )


def build_invoke_services(
    *,
    registry: Registry,
    runtime: BoundOperationRuntime,
    plans: EgPlanStore,
    policy_gate: PolicyGate,
    audit_client_factory: ClientFactory,
    **changes: Any,
) -> InvokeServices:
    """Wire the real EG audit adapter (ARBORA-HOST-R024.1); no fake audit sink.

    ``audit_client_factory`` need not be ``runtime``'s own caller/service
    factory -- the audit log can live on a different graph than operation
    data -- but reusing the same :func:`build_client_factory` output is the
    expected common case.
    """

    audit = EgAuditAdapter(audit_client_factory)
    return InvokeServices(
        registry=registry,
        runtime=runtime,
        plans=plans,
        policy_gate=policy_gate,
        audit_preflight=audit.preflight,
        audit_write=audit.write,
        **changes,
    )
