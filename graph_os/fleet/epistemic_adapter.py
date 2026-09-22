"""Generated epistemic-graph adapter for the GraphOS fleet catalog."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from epistemic_graph.generated.agent_component import (
    AgentComponentContentRequest,
    AgentComponentEntry,
    AgentComponentKind,
    AgentComponentOpCurrent,
    AgentComponentSearchRequest,
    ComponentProvenanceMcpServer,
)
from epistemic_graph.generated.storage import (
    send_agent_component_content,
    send_agent_component_current,
    send_agent_component_search,
)

from graph_os.fleet.catalog_reader import (
    COMMONS_GRAPH,
    COMPONENT_CONTENT_SOURCE,
    COMPONENT_CURRENT_SOURCE,
    COMPONENT_SEARCH_SOURCE,
    SERVER_QUERY_SOURCE,
    ComponentContent,
    ComponentKind,
    ComponentPage,
    ComponentPin,
    ComponentRecord,
    ComponentSearchRequest,
    CurrentComponent,
    ReadContext,
    ReadReceipt,
    ServerPage,
    ServerRegistration,
)

_KINDS = {
    "mcp_server": AgentComponentKind.MCP_SERVER,
    "tool": AgentComponentKind.TOOL,
    "skill": AgentComponentKind.SKILL,
    "mcp_prompt": AgentComponentKind.MCP_PROMPT,
    "mcp_resource": AgentComponentKind.MCP_RESOURCE,
}


class GeneratedFleetCatalogPort:
    """Translate generated EG results into the GraphOS read-domain port.

    ``tenant_client`` and ``commons_client`` are non-owning, request-context
    routed views supplied by the process engine. No identity, token, client, or
    graph selection is discovered here.
    """

    def __init__(
        self,
        *,
        tenant_client: Any,
        commons_client: Any,
        context: ReadContext,
    ) -> None:
        self._tenant = tenant_client
        self._commons = commons_client
        self._context = context

    async def read_context(self) -> ReadContext:
        return self._context

    def _receipt(self, source: str, graph: str) -> ReadReceipt:
        return ReadReceipt(context=self._context, source=source, graph=graph)

    async def query_registered_servers(
        self, graph: str, limit: int, cursor: Any | None, /
    ) -> ServerPage:
        if graph != COMMONS_GRAPH:
            raise ValueError("server registry reads must target __commons__")
        page = await self._commons.server_registry.page(limit=limit, cursor=cursor)
        entries = tuple(self._server(row) for row in page.entries)
        return ServerPage(
            entries=entries,
            next_cursor=page.next_cursor,
            observed_at_ms=page.observed_at_ms,
            total_live=page.total_live,
            registry_revision=page.registry_revision,
            registry_digest=page.registry_digest,
            receipt=self._receipt(SERVER_QUERY_SOURCE, COMMONS_GRAPH),
        )

    async def search_components(
        self, request: ComponentSearchRequest, /
    ) -> ComponentPage:
        if request.tenant_id != self._context.tenant_id:
            raise ValueError("component search crossed the verified tenant")
        page = await send_agent_component_search(
            self._tenant,
            AgentComponentSearchRequest(
                tenant_id=request.tenant_id,
                kinds=[_KINDS[kind] for kind in request.kinds],
                limit=request.limit,
                cursor=request.cursor,
            ),
            self._context.tenant_id,
        )
        return ComponentPage(
            entries=tuple(self._component(entry) for entry in page.entries),
            next_cursor=page.next_cursor,
            receipt=self._receipt(COMPONENT_SEARCH_SOURCE, self._context.tenant_id),
        )

    async def current_component(
        self, *, tenant_id: str, component_id: str
    ) -> CurrentComponent:
        if tenant_id != self._context.tenant_id:
            raise ValueError("component current read crossed the verified tenant")
        result = await send_agent_component_current(
            self._tenant,
            AgentComponentOpCurrent(
                op="current", tenant_id=tenant_id, component_id=component_id
            ),
            tenant_id,
        )
        entry = None if result is None else self._component(result)
        return CurrentComponent(
            entry=entry,
            receipt=self._receipt(COMPONENT_CURRENT_SOURCE, tenant_id),
        )

    async def component_content(
        self, *, tenant_id: str, component_id: str, entry_revision: int
    ) -> ComponentContent:
        if tenant_id != self._context.tenant_id:
            raise ValueError("component content read crossed the verified tenant")
        result = await send_agent_component_content(
            self._tenant,
            AgentComponentContentRequest(
                tenant_id=tenant_id,
                component_id=component_id,
                entry_revision=entry_revision,
            ),
            tenant_id,
        )
        return ComponentContent(
            component_id=result.component_id,
            entry_revision=result.entry_revision,
            definition_digest=result.definition_digest,
            content_digest=result.content_digest,
            media_type=result.media_type,
            body=result.body,
            receipt=self._receipt(COMPONENT_CONTENT_SOURCE, tenant_id),
        )

    @staticmethod
    def _server(row: Any) -> ServerRegistration:
        resources = row.resources
        if not isinstance(resources, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in resources.items()
        ):
            raise RuntimeError("registered-server resources are not string metadata")
        return ServerRegistration(
            server_id=f"srv:{row.name}",
            name=row.name,
            url=row.url,
            resources=tuple(sorted(resources.items())),
            registered_at_ms=row.registered_at_ms,
            last_heartbeat_ms=row.last_heartbeat_ms,
            lease_expires_at_ms=row.lease_expires_at_ms,
        )

    @staticmethod
    def _component(entry: AgentComponentEntry) -> ComponentRecord:
        kind = entry.kind.value
        if kind not in _KINDS:
            raise RuntimeError("EG returned an unrequested fleet component kind")
        connector = (entry.attributes or {}).get("pack.connector")
        if not connector:
            raise RuntimeError("pack-owned fleet component lacks connector identity")
        provenance = entry.provenance
        if isinstance(provenance, ComponentProvenanceMcpServer):
            upstream_name = provenance.upstream_name
            pin = ComponentPin(
                component_id=provenance.server.component_id,
                kind="mcp_server",
                definition_digest=provenance.server.definition_digest,
            )
        else:
            upstream_name = connector
            pin = None
        return ComponentRecord(
            component_id=entry.component_id,
            kind=cast(ComponentKind, kind),
            server_name=connector,
            upstream_name=upstream_name,
            summary=entry.summary,
            tenant_id=entry.tenant_id,
            entry_revision=entry.entry_revision,
            definition_digest=entry.definition_digest,
            content_digest=entry.content_digest,
            lifecycle=entry.lifecycle.value,
            provenance_server=pin,
        )


__all__ = ["GeneratedFleetCatalogPort"]
