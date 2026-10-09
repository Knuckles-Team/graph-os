"""Production ``InvokeServices``/``BoundOperationRuntime`` composition.

GRAPHOS-HOST-R024.1: assembles a real operation runtime from GraphOS's
existing epistemic-graph adapter (:mod:`graph_os.epistemic`), audit adapter
(:mod:`graph_os.api.invoke.eg_audit`), and plan store
(:mod:`graph_os.api.invoke.plan`) -- no fabricated client, fake audit sink,
or fake plan store. This module is tested against EG client fixtures (it
never opens a live connection); wiring a live pool/connect configuration
into the serving process at startup is the separate, later
``graph_os.mcp_server.server`` cutover (GRAPHOS-HOST-R024.2), which this
module does not perform.

Two capabilities genuinely do not exist on the installed ``epistemic_graph``
client yet and fail closed here rather than fabricate behavior:

* :func:`deny_subject_access` -- no registered operation sets
  ``Executor.SERVICE`` today (the curated ``agents``/``browser``/``fleet``
  operations, :mod:`graph_os.api.ops`, are all ``Executor.CALLER``), so this
  is unreachable in practice. It exists only so a ``BoundOperationRuntime``
  has a safe, explicit default before the first service-executor operation
  is added, instead of silently granting access nothing checked.
* :func:`unbridged_eg_dispatch` -- the EG contract's generated ``eg.*``
  operations (:mod:`graph_os.api.registry.eg_binding`) each name a contract
  method id, but the installed ``epistemic_graph`` client exposes no generic
  "call this named method" surface (only ``apply_mutation``, ``reconcile``,
  ``resource_stats``, ``health``, ``ping``, ``supports``) -- there is no
  client method to call yet. Until that bridge exists, an ``eg.*`` operation
  refuses with ``UNAVAILABLE`` instead of guessing at an API shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from graph_os.api.invoke.eg_audit import EgAuditAdapter
from graph_os.api.invoke.executor import (
    BoundOperationRuntime,
    ClientFactory,
    ServiceClaims,
    SubjectCheck,
)
from graph_os.api.invoke.pipeline import InvokeServices
from graph_os.api.invoke.plan import EgPlanStore
from graph_os.api.invoke.steps import VerifiedCaller, freeze_claims
from graph_os.api.policy.eunomia import PolicyGate
from graph_os.api.registry import Registry
from graph_os.epistemic import ClientContext, EpistemicClientPool, VerifiedActor

__all__ = [
    "SessionRequiredError",
    "VerifiedSession",
    "build_client_factory",
    "build_invoke_services",
    "build_operation_runtime",
    "build_service_claims",
    "deny_subject_access",
    "unbridged_eg_dispatch",
    "verify_current_from_session",
]


class SessionRequiredError(RuntimeError):
    """``verify_current_from_session`` found no current, matching session."""


class VerifiedSession:
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

    ``connect_context`` builds the :class:`~graph_os.epistemic.ClientContext`
    used only for the pool's first connection to ``graph`` under a given
    tenant -- GraphOS's own verified process identity, never the per-request
    caller. The per-request caller authority is applied afterward, by
    ``BoundOperationRuntime.as_caller``/``as_service``, through the returned
    client's own ``use_verified_context``.
    """

    async def _client(tenant: str) -> Any:
        return await pool.connected_client(connect_context(tenant), graph)

    return _client


async def verify_current_from_session(caller: VerifiedCaller) -> VerifiedCaller:
    """Reconfirm the caller's session is current; refresh its engine claims.

    Mirrors ``graph_os.mcp_server.runtime.verified_tool_session_scope``'s own
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
    """Mint exact-scope service claims from GraphOS's own verified process actor.

    Reuses :meth:`~graph_os.epistemic.ClientContext.from_actor`, which
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


async def deny_subject_access(_caller: VerifiedCaller, _subject: str) -> bool:
    """Fail closed; no registered operation sets ``Executor.SERVICE`` yet."""

    return False


async def unbridged_eg_dispatch(binding: Any, _params: Any, _context: Any) -> Any:
    """Refuse an EG contract method the installed client cannot call yet."""

    from epistemic_graph import EngineResponseError

    raise EngineResponseError(
        "UNAVAILABLE", f"no client bridge for EG method {binding.service!r}"
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
    eg_dispatch: Callable[[Any, Any, Any], Any] = unbridged_eg_dispatch,
) -> BoundOperationRuntime:
    """Assemble the real caller/service client factories and EG dispatch port."""

    client_factory = build_client_factory(
        pool, graph=graph, connect_context=connect_context
    )
    return BoundOperationRuntime(
        caller_client=client_factory,
        verify_current=verify_current_from_session,
        service_client=client_factory,
        service_claims=service_claims,
        check_access=check_access,
        eg_dispatch=eg_dispatch,
        service_scopes=service_scopes,
        service_principal=service_principal,
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
    """Wire the real EG audit adapter (GRAPHOS-HOST-R024.1); no fake audit sink.

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
