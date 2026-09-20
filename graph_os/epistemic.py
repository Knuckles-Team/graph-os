"""Epistemic-graph client context, pooling, and topology composition.

RF-ADR-009 keeps graph behavior in :mod:`epistemic_graph`.  This module owns
only graph-os process concerns: translating verified actor authority into the
client's request-context shape, keeping one transport client per named graph,
and translating the engine-authored placement/topology responses used for
routing.  It deliberately contains no graph query, mutation, or fleet-catalog
logic.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol, Self

PLACEMENT_READ_SCOPE = "cluster:placement-read"
TOPOLOGY_READ_SCOPE = "cluster:topology-read"
SERVICE_CONTROL_SCOPE = "service:control"


class AuthorityError(PermissionError):
    """Verified actor authority is absent, inconsistent, or insufficient."""


class TenantGraphBindingError(AuthorityError):
    """A named graph was requested under a tenant other than its bound tenant."""


class VerifiedActor(Protocol):
    """The generic subset of an authenticated actor needed by graph-os."""

    actor_id: str
    tenant_id: str
    roles: Iterable[str]
    authenticated: bool

    def ensure_credential_current(self) -> None:
        """Raise when the already-verified credential is no longer current."""


class EpistemicClient(Protocol):
    """The existing EG client operations composed by this adapter."""

    placement: Any
    cluster_topology: Any

    def use_verified_context(self, context: Mapping[str, Any]) -> Any:
        """Bind request-local verified claims and restore the prior binding."""

    async def health(self) -> dict[str, Any]:
        """Return the engine-authored health document."""

    async def close(self) -> None:
        """Close the transport."""


ConnectClient = Callable[..., Awaitable[EpistemicClient]]


def _non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthorityError(f"{name} must be a non-empty string")
    return value


def _string_tuple(name: str, values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings")
    rendered = tuple(values)
    seen: set[str] = set()
    for value in rendered:
        _non_empty(f"{name} entry", value)
        if value in seen:
            raise AuthorityError(f"{name} contains duplicate entry {value!r}")
        seen.add(value)
    return rendered


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Immutable authority in the exact base shape accepted by the EG client."""

    principal: str
    tenant: str
    audience: str
    agent_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    policy_version: str
    delegation: tuple[str, ...] = ()
    priority: str | None = None
    oidc_token: str | None = None

    def __post_init__(self) -> None:
        for name in ("principal", "tenant", "audience", "agent_id", "policy_version"):
            _non_empty(name, getattr(self, name))
        object.__setattr__(self, "roles", _string_tuple("roles", self.roles))
        object.__setattr__(self, "scopes", _string_tuple("scopes", self.scopes))
        object.__setattr__(
            self, "delegation", _string_tuple("delegation", self.delegation)
        )
        for name in ("priority", "oidc_token"):
            value = getattr(self, name)
            if value is not None:
                _non_empty(name, value)
        if self.principal == self.agent_id:
            if self.delegation:
                raise AuthorityError(
                    "delegation must be empty when principal is the effective agent"
                )
        elif (
            len(self.delegation) < 2
            or self.delegation[0] != self.principal
            or self.delegation[-1] != self.agent_id
        ):
            raise AuthorityError(
                "delegation must run from principal to the effective agent"
            )

    @classmethod
    def from_actor(
        cls,
        actor: VerifiedActor,
        *,
        tenant: str,
        audience: str,
        scopes: Iterable[str],
        policy_version: str,
        agent_id: str | None = None,
        delegation: Iterable[str] = (),
        priority: str | None = None,
        oidc_token: str | None = None,
    ) -> Self:
        """Translate one already-verified actor without minting authority.

        The explicit tenant must match the actor's verified tenant.  Credential
        freshness is checked before any claims are detached from the actor.
        """

        if not getattr(actor, "authenticated", False):
            raise AuthorityError(
                "actor authority was not minted from verified identity"
            )
        ensure_current = getattr(actor, "ensure_credential_current", None)
        if not callable(ensure_current):
            raise AuthorityError("verified actor has no credential freshness check")
        ensure_current()
        principal = _non_empty("actor.actor_id", getattr(actor, "actor_id", None))
        actor_tenant = _non_empty("actor.tenant_id", getattr(actor, "tenant_id", None))
        requested_tenant = _non_empty("tenant", tenant)
        if actor_tenant != requested_tenant:
            raise AuthorityError(
                "requested tenant does not match verified actor tenant"
            )
        effective_agent = agent_id or principal
        return cls(
            principal=principal,
            tenant=requested_tenant,
            audience=audience,
            agent_id=effective_agent,
            roles=tuple(getattr(actor, "roles", ())),
            scopes=tuple(scopes),
            policy_version=policy_version,
            delegation=tuple(delegation),
            priority=priority,
            oidc_token=oidc_token,
        )

    def require_scope(self, scope: str) -> None:
        """Fail closed unless this context carries the exact EG policy action."""

        required = _non_empty("scope", scope)
        if required not in self.scopes:
            raise AuthorityError(
                f"verified context is missing required scope {required!r}"
            )

    def with_scopes(self, scopes: Iterable[str]) -> Self:
        """Return a context with an explicitly replaced scope set."""

        return replace(self, scopes=tuple(scopes))

    def to_claims(self) -> dict[str, Any]:
        """Return a detached mapping accepted by ``validate_request_context``."""

        claims: dict[str, Any] = {
            "principal": self.principal,
            "tenant": self.tenant,
            "audience": self.audience,
            "agent_id": self.agent_id,
            "roles": list(self.roles),
            "scopes": list(self.scopes),
            "policy_version": self.policy_version,
            "delegation": list(self.delegation),
        }
        if self.priority is not None:
            claims["priority"] = self.priority
        if self.oidc_token is not None:
            claims["oidc_token"] = self.oidc_token
        return claims


@dataclass(frozen=True, slots=True)
class GraphPlacement:
    """The engine-authored placement fields graph-os uses for one graph."""

    tenant: str
    graph: str
    route_id: str
    placed: bool
    stale: bool
    group: int
    epoch: int
    fencing_token: int
    endpoints: tuple[str, ...]

    @classmethod
    def from_engine(
        cls, payload: Mapping[str, Any], *, tenant: str, graph: str
    ) -> Self:
        """Translate a client-validated placement response and recheck its target."""

        if payload.get("authoritative") is not True:
            raise RuntimeError("engine returned a non-authoritative placement route")
        if payload.get("tenant_ref") != tenant or payload.get("partition_ref") != graph:
            raise TenantGraphBindingError(
                "engine returned placement for a different tenant or graph"
            )
        endpoints = payload.get("endpoints")
        if not isinstance(endpoints, list) or not all(
            isinstance(endpoint, str) and endpoint for endpoint in endpoints
        ):
            raise RuntimeError("engine returned malformed placement endpoints")
        try:
            return cls(
                tenant=tenant,
                graph=graph,
                route_id=_non_empty("route_id", payload["route_id"]),
                placed=payload["placed"],
                stale=payload["stale"],
                group=payload["group"],
                epoch=payload["epoch"],
                fencing_token=payload["fencing_token"],
                endpoints=tuple(endpoints),
            )
        except KeyError as exc:
            raise RuntimeError("engine returned an incomplete placement route") from exc


@dataclass(frozen=True, slots=True)
class ClusterTopology:
    """The identity and monotonic fences from a verified topology snapshot."""

    cluster_id: str
    membership_epoch: int
    placement_epoch: int
    groups: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_engine(cls, payload: Mapping[str, Any]) -> Self:
        try:
            groups = payload["groups"]
            if not isinstance(groups, list):
                raise TypeError
            return cls(
                cluster_id=_non_empty("cluster_id", payload["cluster_id"]),
                membership_epoch=payload["membership_epoch"],
                placement_epoch=payload["placement_epoch"],
                groups=tuple(groups),
            )
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                "engine returned an incomplete topology snapshot"
            ) from exc


class EpistemicClientPool:
    """Own exactly one long-lived EG transport client per named graph.

    A graph becomes tenant-bound on first use.  Reusing the graph name under a
    different tenant is rejected before a client or request context is exposed.
    Every usable client is yielded only inside ``use_verified_context`` so the
    first connection's claims can never become ambient authority for a later
    operation.
    """

    def __init__(self, connect: ConnectClient, /, **connect_kwargs: Any) -> None:
        if "graph_name" in connect_kwargs or "verified_context" in connect_kwargs:
            raise ValueError(
                "graph_name and verified_context are owned by EpistemicClientPool"
            )
        self._connect = connect
        self._connect_kwargs = dict(connect_kwargs)
        self._clients: dict[str, EpistemicClient] = {}
        self._graph_tenants: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def graph_count(self) -> int:
        return len(self._clients)

    async def _client_for(self, context: ClientContext, graph: str) -> EpistemicClient:
        target = _non_empty("graph", graph)
        async with self._lock:
            if self._closed:
                raise RuntimeError("epistemic client pool is closed")
            bound_tenant = self._graph_tenants.get(target)
            if bound_tenant is not None and bound_tenant != context.tenant:
                raise TenantGraphBindingError(
                    f"graph {target!r} is already bound to another tenant"
                )
            existing = self._clients.get(target)
            if existing is not None:
                return existing
            client = await self._connect(
                graph_name=target,
                verified_context=context.to_claims(),
                **self._connect_kwargs,
            )
            self._graph_tenants[target] = context.tenant
            self._clients[target] = client
            return client

    @contextlib.asynccontextmanager
    async def bind(
        self,
        context: ClientContext,
        graph: str,
        *,
        required_scope: str | None = None,
    ) -> AsyncIterator[EpistemicClient]:
        """Yield a graph-bound client under task-local verified authority."""

        if required_scope is not None:
            context.require_scope(required_scope)
        client = await self._client_for(context, graph)
        manager = client.use_verified_context(context.to_claims())
        with manager:
            yield client

    async def close(self) -> None:
        """Close every owned transport once and permanently close the pool."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = tuple(self._clients.values())
            self._clients.clear()
            self._graph_tenants.clear()
        for client in clients:
            await client.close()


class TopologyAdapter:
    """Translate EG placement, topology, and health APIs for graph-os callers."""

    def __init__(self, pool: EpistemicClientPool) -> None:
        self._pool = pool

    async def placement(
        self, context: ClientContext, graph: str, *, client_epoch: int = 0
    ) -> GraphPlacement:
        async with self._pool.bind(
            context, graph, required_scope=PLACEMENT_READ_SCOPE
        ) as client:
            payload = await client.placement.route(
                context.tenant, graph, client_epoch=client_epoch
            )
        return GraphPlacement.from_engine(payload, tenant=context.tenant, graph=graph)

    async def topology(
        self,
        context: ClientContext,
        graph: str,
        *,
        expected_cluster_id: str | None = None,
        min_membership_epoch: int | None = None,
        min_placement_epoch: int | None = None,
    ) -> ClusterTopology:
        async with self._pool.bind(
            context, graph, required_scope=TOPOLOGY_READ_SCOPE
        ) as client:
            payload = await client.cluster_topology.members(
                expected_cluster_id=expected_cluster_id,
                min_membership_epoch=min_membership_epoch,
                min_placement_epoch=min_placement_epoch,
            )
        return ClusterTopology.from_engine(payload)

    async def health(self, context: ClientContext, graph: str) -> Mapping[str, Any]:
        async with self._pool.bind(
            context, graph, required_scope=SERVICE_CONTROL_SCOPE
        ) as client:
            return await client.health()
