"""Native startup composition for EG and AU catalog read authorities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from graph_os.connector_pack_authority import compose_pack_import_authority
from graph_os.fleet.catalog_authority import (
    VerifiedAgentLibraryOwnerPrincipal,
    VerifiedMcpCatalogWriter,
    policy_admitted_catalog_context,
)
from graph_os.fleet.catalog_reader import (
    DeferredFleetCatalogReader,
    FleetCatalogReader,
    ReadContext,
)
from graph_os.fleet.epistemic_adapter import GeneratedFleetCatalogPort
from graph_os.gateway.enhanced_catalog_api import (
    AgentCatalogReadPort,
    EnhancedCatalogAuthority,
    WorkflowCatalogReadPort,
    configure_enhanced_catalog,
)
from graph_os.mcp_server.remote_pack_authority import RemoteConnectorPackAuthority
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
) -> FleetCatalogReader:
    """Install and prove every catalog authority before service readiness.

    The process engine supplies non-owning, session-routed generated-client
    views. The initial multiplexer refresh is mandatory: an unavailable or
    inconsistent EG/AU authority aborts startup and no static declaration is
    ever consulted.
    """

    context = _read_context(session)
    compute = engine.graph_compute
    tenant_client = compute.for_graph(context.tenant_id).async_client
    commons_client = compute.for_graph("__commons__").async_client
    port = GeneratedFleetCatalogPort(
        tenant_client=tenant_client,
        commons_client=commons_client,
        context=context,
    )
    reader = FleetCatalogReader(port)
    await verify_semantic_content(
        client=tenant_client,
        tenant_id=context.tenant_id,
        graph=context.tenant_id,
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
    multiplexer.install_catalog_authority_writer(
        VerifiedMcpCatalogWriter(
            fleet_port=port,
            mounted_catalog=multiplexer,
            current_context=lambda: _read_context(session),
            mutation_context=policy_admitted_catalog_context(engine, session),
        )
    )
    multiplexer.install_connector_pack_authority(
        compose_pack_import_authority(
            engine,
            session,
            mounted_catalog=multiplexer,
            owner_principal=VerifiedAgentLibraryOwnerPrincipal(
                fleet_port=port,
                mounted_catalog=multiplexer,
                current_context=lambda: _read_context(session),
            ),
        )
    )
    multiplexer.install_remote_connector_pack_authority(
        RemoteConnectorPackAuthority(
            engine=engine,
            attester_session=session,
            mounted_catalog=multiplexer,
        )
    )
    return reader


__all__ = ["compose_catalog_authorities"]
