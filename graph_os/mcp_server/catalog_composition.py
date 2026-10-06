"""Native startup composition for EG and AU catalog read authorities."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from graph_os.fleet.catalog_reader import (
    DeferredFleetCatalogReader,
    FleetCatalogReader,
    ReadContext,
)
from graph_os.fleet.epistemic_adapter import GeneratedFleetCatalogPort, GraphBinder
from graph_os.gateway.enhanced_catalog_api import (
    AgentCatalogReadPort,
    EnhancedCatalogAuthority,
    WorkflowCatalogReadPort,
    configure_enhanced_catalog,
)
from graph_os.semantic_content import (
    required_content_connectors,
    verify_semantic_content,
)

CatalogPortsFactory = Callable[
    [Any, Any], tuple[WorkflowCatalogReadPort, AgentCatalogReadPort]
]


def _public_au_catalog_ports(
    engine: Any, session: Any
) -> tuple[WorkflowCatalogReadPort, AgentCatalogReadPort]:
    """Resolve only AU's public execution/control-plane application seam."""

    from agent_utilities.api import catalog_read_ports

    return catalog_read_ports(engine, session)


def _bind_session_graph(graph: str) -> AbstractContextManager[object]:
    """Narrow the ambient verified session to ``graph`` for one engine read.

    AU's graph views refuse a session that targets another graph, so a read of
    ``__commons__`` (or of the tenant graph from a session elsewhere) runs under
    the same verified authority re-targeted to that graph.
    """

    from agent_utilities.api.session import resolve_session, use_session

    return use_session(resolve_session().with_graph(graph))


def _read_context(session: Any) -> ReadContext:
    claims = session.engine_verified_context()
    return ReadContext(
        tenant_id=str(claims["tenant"]),
        principal_id=str(claims["principal"]),
        agent_id=str(claims["agent_id"]),
        audience=str(claims["audience"]),
        policy_version=str(claims["policy_version"]),
    )


async def compose_catalog_authorities(
    *,
    engine: Any,
    session: Any,
    deferred_fleet: DeferredFleetCatalogReader,
    multiplexer: Any,
    catalog_ports_factory: CatalogPortsFactory = _public_au_catalog_ports,
    bind_graph: GraphBinder = _bind_session_graph,
) -> FleetCatalogReader:
    """Install and prove every catalog authority before service readiness.

    The process engine supplies non-owning, session-routed generated-client
    views. The initial multiplexer refresh is mandatory: an unavailable or
    inconsistent EG/AU authority aborts startup and no static declaration is
    ever consulted.
    """

    context = _read_context(session)
    # The tenant's data graph is the verified session's graph (AU's tenant
    # graph naming), never the bare tenant id.
    tenant_graph = str(session.graph)
    compute = engine.graph_compute
    tenant_client = compute.for_graph(tenant_graph).async_client
    commons_client = compute.for_graph("__commons__").async_client
    reader = FleetCatalogReader(
        GeneratedFleetCatalogPort(
            tenant_client=tenant_client,
            commons_client=commons_client,
            context=context,
            tenant_graph=tenant_graph,
            bind_graph=bind_graph,
        )
    )
    with bind_graph(tenant_graph):
        await verify_semantic_content(
            client=tenant_client,
            tenant_id=context.tenant_id,
            graph=tenant_graph,
            connectors=required_content_connectors(),
        )
    workflows, agents = catalog_ports_factory(engine, session)
    deferred_fleet.install(reader)
    configure_enhanced_catalog(
        EnhancedCatalogAuthority(
            fleet=reader,
            workflows=workflows,
            agents=agents,
        )
    )
    await multiplexer.refresh_engine_catalog()
    return reader


__all__ = ["compose_catalog_authorities"]
