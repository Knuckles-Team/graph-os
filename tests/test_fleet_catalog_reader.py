"""Focused contract tests for the EG-native fleet catalog reader."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from graph_os.fleet.catalog_reader import (
    COMMONS_GRAPH,
    COMPONENT_CONTENT_SOURCE,
    COMPONENT_CURRENT_SOURCE,
    COMPONENT_SEARCH_SOURCE,
    FLEET_COMPONENT_KINDS,
    SERVER_QUERY_SOURCE,
    ComponentContent,
    ComponentPage,
    ComponentPin,
    ComponentRecord,
    ComponentSearchRequest,
    CurrentComponent,
    DeferredFleetCatalogReader,
    FleetCatalogIntegrityError,
    FleetCatalogReader,
    ReadContext,
    ReadReceipt,
    ServerPage,
    ServerRegistration,
)

CONTEXT = ReadContext(
    tenant_id="tenant-a",
    principal_id="principal-a",
    agent_id="agent-a",
    audience="graph-os",
    policy_version="policy-v1",
)
OTHER_CONTEXT = replace(CONTEXT, tenant_id="tenant-b")
SERVER_DEFINITION_DIGEST = "sha256:" + "a" * 64
SERVER_CONTENT_DIGEST = "sha256:" + "b" * 64
TOOL_DEFINITION_DIGEST = "sha256:" + "c" * 64
TOOL_CONTENT_DIGEST = "sha256:" + "d" * 64


def receipt(source: str, graph: str, context: ReadContext = CONTEXT) -> ReadReceipt:
    return ReadReceipt(context=context, source=source, graph=graph)


def server_page(
    *entries: ServerRegistration,
    cursor: str | None = None,
    context: ReadContext = CONTEXT,
    total_live: int | None = None,
) -> ServerPage:
    return ServerPage(
        entries=tuple(entries),
        next_cursor=cursor,
        observed_at_ms=3_000,
        total_live=len(entries) if total_live is None else total_live,
        registry_revision=1,
        registry_digest="sha256:" + "9" * 64,
        receipt=receipt(SERVER_QUERY_SOURCE, COMMONS_GRAPH, context),
    )


def component_page(
    *entries: ComponentRecord,
    cursor: str | None = None,
    context: ReadContext = CONTEXT,
) -> ComponentPage:
    return ComponentPage(
        entries=tuple(entries),
        next_cursor=cursor,
        receipt=receipt(COMPONENT_SEARCH_SOURCE, context.tenant_id, context),
    )


SERVER = ServerRegistration(
    server_id="srv:search-mcp",
    name="search-mcp",
    url="mcp-ref://search-mcp",
    resources=(("transport", "streamable-http"),),
    registered_at_ms=1_000,
    last_heartbeat_ms=2_000,
    lease_expires_at_ms=302_000,
)
SERVER_COMPONENT = ComponentRecord(
    component_id="mcp:search/mcp_server/search-mcp",
    kind="mcp_server",
    server_name="search-mcp",
    upstream_name="search-mcp",
    summary="Search server",
    tenant_id="tenant-a",
    entry_revision=3,
    definition_digest=SERVER_DEFINITION_DIGEST,
    content_digest=SERVER_CONTENT_DIGEST,
    lifecycle="published",
    provenance_server=None,
)
SERVER_PIN = ComponentPin(
    component_id=SERVER_COMPONENT.component_id,
    kind="mcp_server",
    definition_digest=SERVER_COMPONENT.definition_digest,
)
TOOL_COMPONENT = ComponentRecord(
    component_id="mcp:search/tool/search",
    kind="tool",
    server_name="search-mcp",
    upstream_name="search",
    summary="Search documents",
    tenant_id="tenant-a",
    entry_revision=7,
    definition_digest=TOOL_DEFINITION_DIGEST,
    content_digest=TOOL_CONTENT_DIGEST,
    lifecycle="published",
    provenance_server=SERVER_PIN,
)


def content(entry: ComponentRecord) -> ComponentContent:
    return ComponentContent(
        component_id=entry.component_id,
        entry_revision=entry.entry_revision,
        definition_digest=entry.definition_digest,
        content_digest=entry.content_digest,
        media_type="application/json",
        body=(entry.upstream_name + "-body").encode(),
        receipt=receipt(COMPONENT_CONTENT_SOURCE, CONTEXT.tenant_id),
    )


def captured_content(entry: ComponentRecord) -> ComponentContent:
    """The SDK's pinned MCP_SERVER body captured from initialization."""
    return replace(
        content(entry),
        body=json.dumps({"name": entry.upstream_name, "version": "1.0.0"}).encode(),
    )


def catalog_content(entry: ComponentRecord) -> ComponentContent:
    return captured_content(entry) if entry.kind == "mcp_server" else content(entry)


class FakeFleetCatalogPort:
    """Deterministic fake of the pending generated EG adapter."""

    def __init__(
        self,
        *,
        server_pages: list[ServerPage] | None = None,
        component_pages: list[ComponentPage] | None = None,
    ) -> None:
        self.context = CONTEXT
        self.server_pages = list(server_pages or [server_page(SERVER)])
        self.component_pages = list(
            component_pages or [component_page(SERVER_COMPONENT, TOOL_COMPONENT)]
        )
        entries = [entry for page in self.component_pages for entry in page.entries]
        self.current = {entry.component_id: entry for entry in entries}
        self.contents = {
            entry.component_id: catalog_content(entry) for entry in entries
        }
        self.server_calls: list[tuple[str, int, str | None]] = []
        self.search_calls: list[ComponentSearchRequest] = []

    async def read_context(self) -> ReadContext:
        return self.context

    async def query_registered_servers(
        self, graph: str, limit: int, cursor: str | None, /
    ) -> ServerPage:
        self.server_calls.append((graph, limit, cursor))
        return self.server_pages.pop(0)

    async def search_components(self, request: ComponentSearchRequest) -> ComponentPage:
        self.search_calls.append(request)
        return self.component_pages.pop(0)

    async def current_component(
        self, *, tenant_id: str, component_id: str
    ) -> CurrentComponent:
        return CurrentComponent(
            entry=self.current.get(component_id),
            receipt=receipt(COMPONENT_CURRENT_SOURCE, tenant_id),
        )

    async def component_content(
        self, *, tenant_id: str, component_id: str, entry_revision: int
    ) -> ComponentContent:
        return self.contents[component_id]


def test_reader_joins_liveness_current_records_and_content_with_bounded_pages() -> None:
    port = FakeFleetCatalogPort(
        server_pages=[
            server_page(cursor="server-2", total_live=1),
            server_page(SERVER, total_live=1),
        ],
        component_pages=[
            component_page(SERVER_COMPONENT, cursor="component-2"),
            component_page(TOOL_COMPONENT),
        ],
    )

    catalog = asyncio.run(FleetCatalogReader(port, page_size=2).read())

    assert catalog.context == CONTEXT
    assert catalog.registry_revision == 1
    assert catalog.registry_digest == "sha256:" + "9" * 64
    assert len(catalog.servers) == 1
    joined = catalog.servers[0]
    assert joined.registration == SERVER
    assert joined.component == SERVER_COMPONENT
    assert [item.entry for item in joined.provides] == [TOOL_COMPONENT]
    assert joined.provides[0].content.body == b"search-body"
    assert port.server_calls == [
        (COMMONS_GRAPH, 2, None),
        (COMMONS_GRAPH, 2, "server-2"),
    ]
    assert port.search_calls == [
        ComponentSearchRequest("tenant-a", FLEET_COMPONENT_KINDS, 2, None),
        ComponentSearchRequest("tenant-a", FLEET_COMPONENT_KINDS, 2, "component-2"),
    ]


def test_reader_joins_runtime_name_from_pinned_content_across_pack_identity() -> None:
    pack_server = replace(
        SERVER_COMPONENT, server_name="search", upstream_name="search"
    )
    pack_tool = replace(TOOL_COMPONENT, server_name="search")
    port = FakeFleetCatalogPort(
        component_pages=[component_page(pack_server, pack_tool)]
    )
    port.contents[pack_server.component_id] = replace(
        content(pack_server), body=b'{"name":"search-mcp","version":"1.0.0"}'
    )

    catalog = asyncio.run(FleetCatalogReader(port).read())

    assert catalog.servers[0].registration == SERVER
    assert catalog.servers[0].component.server_name == "search-mcp"
    assert catalog.servers[0].provides[0].entry.server_name == "search-mcp"


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"name":"","version":"1.0.0"}',
        b'{"name":"search-mcp","name":"other","version":"1.0.0"}',
        b"not-json",
    ],
)
def test_reader_rejects_missing_or_malformed_server_identity(body: bytes) -> None:
    port = FakeFleetCatalogPort()
    port.contents[SERVER_COMPONENT.component_id] = replace(
        content(SERVER_COMPONENT), body=body
    )

    with pytest.raises(FleetCatalogIntegrityError, match="server content"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_rejects_two_packs_claiming_one_runtime_name() -> None:
    other_server = replace(
        SERVER_COMPONENT,
        component_id="mcp:other/mcp_server/other",
        server_name="other",
        upstream_name="other",
    )
    port = FakeFleetCatalogPort(
        component_pages=[component_page(SERVER_COMPONENT, other_server)]
    )
    port.contents[other_server.component_id] = replace(
        content(other_server), body=captured_content(SERVER_COMPONENT).body
    )

    with pytest.raises(FleetCatalogIntegrityError, match="multiple server components"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_does_not_alias_an_unmatched_live_registration() -> None:
    port = FakeFleetCatalogPort()
    port.contents[SERVER_COMPONENT.component_id] = replace(
        content(SERVER_COMPONENT),
        body=b'{"name":"other-mcp","version":"1.0.0"}',
    )

    with pytest.raises(FleetCatalogIntegrityError, match="has no current mcp_server"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_fails_closed_on_cross_tenant_search_receipt() -> None:
    port = FakeFleetCatalogPort(
        component_pages=[
            component_page(SERVER_COMPONENT, TOOL_COMPONENT, context=OTHER_CONTEXT)
        ]
    )

    with pytest.raises(FleetCatalogIntegrityError, match="mismatched context"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_fails_closed_when_search_entry_is_no_longer_current() -> None:
    port = FakeFleetCatalogPort()
    port.current[TOOL_COMPONENT.component_id] = replace(
        TOOL_COMPONENT, entry_revision=8
    )

    with pytest.raises(FleetCatalogIntegrityError, match="changed during catalog read"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_fails_closed_on_unresolved_server_provenance_pin() -> None:
    bad_tool = replace(
        TOOL_COMPONENT,
        provenance_server=replace(
            SERVER_PIN,
            definition_digest="sha256:" + "e" * 64,
        ),
    )
    port = FakeFleetCatalogPort(
        component_pages=[component_page(SERVER_COMPONENT, bad_tool)]
    )

    with pytest.raises(FleetCatalogIntegrityError, match="unresolved server pin"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_fails_closed_on_content_revision_mismatch() -> None:
    port = FakeFleetCatalogPort()
    port.contents[TOOL_COMPONENT.component_id] = replace(
        content(TOOL_COMPONENT), content_digest="sha256:" + "f" * 64
    )

    with pytest.raises(FleetCatalogIntegrityError, match="does not match"):
        asyncio.run(FleetCatalogReader(port).read())


def test_reader_rejects_cyclic_cursor_without_unbounded_io() -> None:
    port = FakeFleetCatalogPort(
        server_pages=[server_page(cursor="repeat"), server_page(cursor="repeat")]
    )

    with pytest.raises(FleetCatalogIntegrityError, match="cyclic"):
        asyncio.run(FleetCatalogReader(port).read())
    assert len(port.server_calls) == 2


def test_deferred_reader_has_no_static_or_uninstalled_fallback() -> None:
    deferred = DeferredFleetCatalogReader()

    with pytest.raises(RuntimeError, match="not installed"):
        asyncio.run(deferred.read())

    reader = FleetCatalogReader(FakeFleetCatalogPort())
    deferred.install(reader)
    catalog = asyncio.run(deferred.read())
    assert catalog.context == CONTEXT

    with pytest.raises(RuntimeError, match="already installed"):
        deferred.install(reader)
