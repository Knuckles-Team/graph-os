"""Typed epistemic-graph fleet-catalog read and join seam.

``RegisterServer``-backed ``:Server`` rows in ``__commons__`` answer which
servers are live. ``AgentComponent.Search`` / ``Current`` / ``Content`` answer
what those servers provide. This module joins those two authorities without
an SQL, change-envelope, static-file, or live-probe fallback.

The protocol is the temporary integration boundary while epistemic-graph's
generated Python request adds kind-only ``AgentComponent.Search``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

COMMONS_GRAPH: Final = "__commons__"
SERVER_QUERY_SOURCE: Final = "RegisterServer:ServerQuery"
COMPONENT_SEARCH_SOURCE: Final = "AgentComponent.Search"
COMPONENT_CURRENT_SOURCE: Final = "AgentComponent.Current"
COMPONENT_CONTENT_SOURCE: Final = "AgentComponent.Content"

ComponentKind = Literal["mcp_server", "tool", "skill", "mcp_prompt", "mcp_resource"]
FLEET_COMPONENT_KINDS: Final[tuple[ComponentKind, ...]] = (
    "mcp_server",
    "tool",
    "skill",
    "mcp_prompt",
    "mcp_resource",
)


class FleetCatalogIntegrityError(RuntimeError):
    """The two EG authorities could not be joined without ambiguity."""


@dataclass(frozen=True, slots=True)
class ReadContext:
    """Verified EG identity whose exact binding every read must carry."""

    tenant_id: str
    principal_id: str
    agent_id: str
    audience: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class ReadReceipt:
    """Context and source provenance attached to one port response."""

    context: ReadContext
    source: str
    graph: str


@dataclass(frozen=True, slots=True)
class ServerRegistration:
    """One live ``RegisterServer``-owned ``:Server`` projection."""

    server_id: str
    name: str
    url: str
    resources: tuple[tuple[str, str], ...]
    registered_at_ms: int
    last_heartbeat_ms: int
    lease_expires_at_ms: int


@dataclass(frozen=True, slots=True)
class ServerPage:
    """One bounded page from the ``__commons__`` server query."""

    entries: tuple[ServerRegistration, ...]
    next_cursor: str | None
    observed_at_ms: int
    receipt: ReadReceipt


@dataclass(frozen=True, slots=True)
class ComponentPin:
    """Pinned identity used by MCP-origin component provenance."""

    component_id: str
    kind: ComponentKind
    definition_digest: str


@dataclass(frozen=True, slots=True)
class ComponentRecord:
    """Normalized current AgentComponent entry needed by fleet consumers."""

    component_id: str
    kind: ComponentKind
    server_name: str
    upstream_name: str
    summary: str
    tenant_id: str
    entry_revision: int
    definition_digest: str
    content_digest: str
    lifecycle: str
    provenance_server: ComponentPin | None


@dataclass(frozen=True, slots=True)
class ComponentSearchRequest:
    """Exact shape graph-os needs from pending kind-only EG search."""

    tenant_id: str
    kinds: tuple[ComponentKind, ...]
    limit: int
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ComponentPage:
    """One bounded AgentComponent.Search page."""

    entries: tuple[ComponentRecord, ...]
    next_cursor: str | None
    receipt: ReadReceipt


@dataclass(frozen=True, slots=True)
class CurrentComponent:
    """AgentComponent.Current result with its verified read receipt."""

    entry: ComponentRecord | None
    receipt: ReadReceipt


@dataclass(frozen=True, slots=True)
class ComponentContent:
    """AgentComponent.Content result pinned to one exact revision."""

    component_id: str
    entry_revision: int
    definition_digest: str
    content_digest: str
    media_type: str
    body: bytes
    receipt: ReadReceipt


@dataclass(frozen=True, slots=True)
class CatalogComponent:
    """A current component and its verified engine-owned content."""

    entry: ComponentRecord
    content: ComponentContent


@dataclass(frozen=True, slots=True)
class CatalogServer:
    """A live registration joined to its current component surface."""

    registration: ServerRegistration
    component: ComponentRecord
    content: ComponentContent
    provides: tuple[CatalogComponent, ...]


@dataclass(frozen=True, slots=True)
class FleetCatalog:
    """Tenant-bound joined view for loader, REST, and MCP adapters."""

    context: ReadContext
    servers: tuple[CatalogServer, ...]


class FleetCatalogReadPort(Protocol):
    """One long-lived EG client adapter used by ``FleetCatalogReader``.

    The eventual adapter owns one ``EpistemicGraphClient`` instance. Its
    server method queries only ``__commons__`` ``:Server`` rows written by
    ``RegisterServer``; the others map 1:1 to AgentComponent operations.
    """

    async def read_context(self) -> ReadContext: ...

    async def query_registered_servers(
        self, *, graph: str, limit: int, cursor: str | None
    ) -> ServerPage: ...

    async def search_components(
        self, request: ComponentSearchRequest
    ) -> ComponentPage: ...

    async def current_component(
        self, *, tenant_id: str, component_id: str
    ) -> CurrentComponent: ...

    async def component_content(
        self, *, tenant_id: str, component_id: str, entry_revision: int
    ) -> ComponentContent: ...


class FleetCatalogReader:
    """Read and verify one complete EG-native fleet catalog snapshot."""

    def __init__(
        self,
        port: FleetCatalogReadPort,
        *,
        page_size: int = 128,
        max_pages: int = 64,
    ) -> None:
        if page_size < 1 or page_size > 256:
            raise ValueError("page_size must be between 1 and 256")
        if max_pages < 1 or max_pages > 256:
            raise ValueError("max_pages must be between 1 and 256")
        self._port = port
        self._page_size = page_size
        self._max_pages = max_pages

    async def read(
        self,
        *,
        kinds: Sequence[ComponentKind] = FLEET_COMPONENT_KINDS,
    ) -> FleetCatalog:
        """Return a fully verified, deterministic live fleet snapshot."""
        selected_kinds = self._validated_kinds(kinds)
        context = await self._port.read_context()
        self._validate_context(context)
        registrations = await self._read_servers(context)
        components = await self._read_components(context, selected_kinds)
        return await self._join(context, registrations, components)

    @staticmethod
    def _validated_kinds(kinds: Sequence[ComponentKind]) -> tuple[ComponentKind, ...]:
        chosen = tuple(dict.fromkeys(kinds))
        if not chosen or "mcp_server" not in chosen:
            raise ValueError("fleet reads must include the mcp_server kind")
        if any(kind not in FLEET_COMPONENT_KINDS for kind in chosen):
            raise ValueError("fleet read contains an unsupported component kind")
        return chosen

    @staticmethod
    def _validate_context(context: ReadContext) -> None:
        claims = (
            context.tenant_id,
            context.principal_id,
            context.agent_id,
            context.audience,
            context.policy_version,
        )
        if any(not claim.strip() for claim in claims):
            raise FleetCatalogIntegrityError("verified read context is incomplete")

    @staticmethod
    def _verify_receipt(
        receipt: ReadReceipt,
        context: ReadContext,
        *,
        source: str,
        graph: str,
    ) -> None:
        if receipt != ReadReceipt(context=context, source=source, graph=graph):
            raise FleetCatalogIntegrityError(
                f"{source} response has mismatched context or provenance"
            )

    async def _read_servers(
        self, context: ReadContext
    ) -> dict[str, ServerRegistration]:
        servers: dict[str, ServerRegistration] = {}
        server_ids: set[str] = set()
        async for page in self._server_pages():
            self._verify_receipt(
                page.receipt,
                context,
                source=SERVER_QUERY_SOURCE,
                graph=COMMONS_GRAPH,
            )
            for server in page.entries:
                self._validate_server(server, observed_at_ms=page.observed_at_ms)
                if server.name in servers or server.server_id in server_ids:
                    raise FleetCatalogIntegrityError(
                        f"duplicate live server registration {server.name!r}"
                    )
                servers[server.name] = server
                server_ids.add(server.server_id)
        return servers

    async def _read_components(
        self,
        context: ReadContext,
        kinds: tuple[ComponentKind, ...],
    ) -> dict[str, ComponentRecord]:
        components: dict[str, ComponentRecord] = {}
        async for page in self._component_pages(context, kinds):
            self._verify_receipt(
                page.receipt,
                context,
                source=COMPONENT_SEARCH_SOURCE,
                graph=context.tenant_id,
            )
            for entry in page.entries:
                self._validate_component(entry, context, kinds)
                if entry.component_id in components:
                    raise FleetCatalogIntegrityError(
                        f"duplicate component {entry.component_id!r}"
                    )
                current = await self._port.current_component(
                    tenant_id=context.tenant_id,
                    component_id=entry.component_id,
                )
                self._verify_receipt(
                    current.receipt,
                    context,
                    source=COMPONENT_CURRENT_SOURCE,
                    graph=context.tenant_id,
                )
                if current.entry != entry:
                    raise FleetCatalogIntegrityError(
                        f"component {entry.component_id!r} changed during catalog read"
                    )
                components[entry.component_id] = entry
        return components

    async def _join(
        self,
        context: ReadContext,
        registrations: dict[str, ServerRegistration],
        components: dict[str, ComponentRecord],
    ) -> FleetCatalog:
        server_components: dict[str, ComponentRecord] = {}
        children: dict[str, list[ComponentRecord]] = {}
        for component in components.values():
            if component.kind == "mcp_server":
                if component.server_name in server_components:
                    raise FleetCatalogIntegrityError(
                        f"multiple server components claim {component.server_name!r}"
                    )
                server_components[component.server_name] = component
                continue
            pin = component.provenance_server
            if pin is None:
                raise FleetCatalogIntegrityError(
                    f"component {component.component_id!r} lacks MCP provenance"
                )
            parent = components.get(pin.component_id)
            if parent is None or not self._pin_matches(pin, parent):
                raise FleetCatalogIntegrityError(
                    f"component {component.component_id!r} has an unresolved server pin"
                )
            if parent.server_name != component.server_name:
                raise FleetCatalogIntegrityError(
                    f"component {component.component_id!r} disagrees on server identity"
                )
            children.setdefault(parent.component_id, []).append(component)

        joined: list[CatalogServer] = []
        for name, registration in registrations.items():
            server_component = server_components.get(name)
            if server_component is None:
                raise FleetCatalogIntegrityError(
                    f"live server {name!r} has no current mcp_server component"
                )
            server_content = await self._read_content(context, server_component)
            provided = []
            ordered_children = sorted(
                children.get(server_component.component_id, []),
                key=lambda item: (item.kind, item.upstream_name, item.component_id),
            )
            for child in ordered_children:
                provided.append(
                    CatalogComponent(
                        entry=child,
                        content=await self._read_content(context, child),
                    )
                )
            joined.append(
                CatalogServer(
                    registration=registration,
                    component=server_component,
                    content=server_content,
                    provides=tuple(provided),
                )
            )
        joined.sort(key=lambda item: item.registration.name)
        return FleetCatalog(context=context, servers=tuple(joined))

    async def _read_content(
        self, context: ReadContext, entry: ComponentRecord
    ) -> ComponentContent:
        content = await self._port.component_content(
            tenant_id=context.tenant_id,
            component_id=entry.component_id,
            entry_revision=entry.entry_revision,
        )
        self._verify_receipt(
            content.receipt,
            context,
            source=COMPONENT_CONTENT_SOURCE,
            graph=context.tenant_id,
        )
        identity = (
            content.component_id == entry.component_id
            and content.entry_revision == entry.entry_revision
            and content.definition_digest == entry.definition_digest
            and content.content_digest == entry.content_digest
        )
        if not identity:
            raise FleetCatalogIntegrityError(
                f"content for {entry.component_id!r} does not match "
                "its current revision"
            )
        return content

    async def _server_pages(self) -> AsyncIterator[ServerPage]:
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self._max_pages):
            page = await self._port.query_registered_servers(
                graph=COMMONS_GRAPH,
                limit=self._page_size,
                cursor=cursor,
            )
            if len(page.entries) > self._page_size or page.observed_at_ms <= 0:
                raise FleetCatalogIntegrityError("server query violated its page bound")
            yield page
            cursor = self._next_cursor(page.next_cursor, seen)
            if cursor is None:
                return
        raise FleetCatalogIntegrityError("server query exceeded its page bound")

    async def _component_pages(
        self, context: ReadContext, kinds: tuple[ComponentKind, ...]
    ) -> AsyncIterator[ComponentPage]:
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self._max_pages):
            page = await self._port.search_components(
                ComponentSearchRequest(
                    tenant_id=context.tenant_id,
                    kinds=kinds,
                    limit=self._page_size,
                    cursor=cursor,
                )
            )
            if len(page.entries) > self._page_size:
                raise FleetCatalogIntegrityError(
                    "component search violated its page bound"
                )
            yield page
            cursor = self._next_cursor(page.next_cursor, seen)
            if cursor is None:
                return
        raise FleetCatalogIntegrityError("component search exceeded its page bound")

    @staticmethod
    def _next_cursor(cursor: str | None, seen: set[str]) -> str | None:
        if cursor is None:
            return None
        if not cursor or len(cursor) > 4096 or cursor in seen:
            raise FleetCatalogIntegrityError(
                "page cursor is empty, oversized, or cyclic"
            )
        seen.add(cursor)
        return cursor

    @staticmethod
    def _validate_server(server: ServerRegistration, *, observed_at_ms: int) -> None:
        malformed = (
            not server.server_id.startswith("srv:")
            or server.server_id != f"srv:{server.name}"
            or not server.name
            or not server.url
            or server.registered_at_ms <= 0
            or server.last_heartbeat_ms < server.registered_at_ms
            or server.lease_expires_at_ms <= server.last_heartbeat_ms
            or server.lease_expires_at_ms <= observed_at_ms
        )
        if malformed:
            raise FleetCatalogIntegrityError("malformed live server registration")

    @staticmethod
    def _validate_component(
        entry: ComponentRecord,
        context: ReadContext,
        kinds: tuple[ComponentKind, ...],
    ) -> None:
        malformed = (
            entry.tenant_id != context.tenant_id
            or entry.kind not in kinds
            or entry.lifecycle != "published"
            or entry.entry_revision < 1
            or not entry.component_id
            or not entry.server_name
            or not FleetCatalogReader._is_digest(entry.definition_digest)
            or not FleetCatalogReader._is_digest(entry.content_digest)
        )
        if malformed:
            raise FleetCatalogIntegrityError(
                f"component {entry.component_id!r} is malformed or out of scope"
            )
        if entry.kind == "mcp_server":
            if entry.provenance_server is not None:
                raise FleetCatalogIntegrityError(
                    f"server component {entry.component_id!r} cannot serve itself"
                )
        elif entry.provenance_server is None:
            raise FleetCatalogIntegrityError(
                f"component {entry.component_id!r} lacks MCP provenance"
            )

    @staticmethod
    def _pin_matches(pin: ComponentPin, entry: ComponentRecord) -> bool:
        return (
            pin.component_id == entry.component_id
            and pin.kind == "mcp_server"
            and entry.kind == "mcp_server"
            and pin.definition_digest == entry.definition_digest
        )

    @staticmethod
    def _is_digest(value: str) -> bool:
        prefix = "sha256:"
        if len(value) != len(prefix) + 64 or not value.startswith(prefix):
            return False
        digest = value[len(prefix) :]
        return all(character in "0123456789abcdef" for character in digest)
