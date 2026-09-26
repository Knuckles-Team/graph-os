"""Join AU pack policy to the current mounted connector catalog authority.

The EG owner principal has no public authenticated read contract yet.  A
deployment must provide that source explicitly; this module never derives it
from the GraphOS actor, request body, or process configuration.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent_utilities.api.provisioning import (
    PackImportAuthorityResolver,
    pack_import_authority,
)
from agent_utilities.knowledge_graph.core.session import resolve_session
from epistemic_graph.generated.connector_pack import McpCatalogSnapshotBinding


class MountedPackCatalog(Protocol):
    async def reconcile_pack_catalog_binding(
        self, server_name: str
    ) -> McpCatalogSnapshotBinding: ...


class AuthenticatedOwnerPrincipal(Protocol):
    """EG-backed read of the current AgentLibrary owner for one tenant."""

    async def serving_principal(self, server_name: str, tenant_id: str) -> str: ...


def compose_pack_import_authority(
    engine: Any,
    session: Any,
    *,
    mounted_catalog: MountedPackCatalog,
    owner_principal: AuthenticatedOwnerPrincipal,
) -> PackImportAuthorityResolver:
    """Resolve each SDK import against the exact connector and ambient actor.

    AU performs the verified-session and policy-receipt gate before either
    provider runs.  EG issues the catalog binding; a separately authenticated
    EG owner read must supply the principal.  Absent ports prevent composition.
    """

    if not callable(getattr(mounted_catalog, "reconcile_pack_catalog_binding", None)):
        raise TypeError("mounted catalog reconciliation is required")
    if not callable(getattr(owner_principal, "serving_principal", None)):
        raise TypeError("authenticated EG owner principal source is required")

    async def resolve(connector: str):
        # AU validates the connector before invoking either closure.  Capture
        # its canonical spelling so a whitespace alias cannot select a
        # different mounted child than the policy target.
        server_name = connector.strip() if isinstance(connector, str) else connector

        async def binding() -> McpCatalogSnapshotBinding:
            return await mounted_catalog.reconcile_pack_catalog_binding(server_name)

        async def principal() -> str:
            verified = resolve_session(session, required_scope="agent:pack-control")
            return await owner_principal.serving_principal(server_name, verified.tenant)

        authority = pack_import_authority(
            engine,
            session,
            catalog_binding=binding,
            serving_principal=principal,
        )
        return await authority(connector)

    return resolve


__all__ = [
    "AuthenticatedOwnerPrincipal",
    "MountedPackCatalog",
    "compose_pack_import_authority",
]
