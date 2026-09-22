"""Typed browser catalog projected from GraphOS-owned authorities.

AgentComponent is authoritative for current MCP servers, tools, and skills.
The workflow and agent control-plane catalogs retain their own identities.  The
transport joins those authorities without filesystem discovery, live MCP
probing, synthetic counts, or a compatibility payload.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, cast

from agent_utilities.api import (
    AgentCatalogReadPort,
    AgentCatalogRecord,
    CatalogStatus,
    WorkflowCatalogReadPort,
    WorkflowCatalogRecord,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from graph_os.fleet.catalog_reader import FleetCatalog, FleetCatalogReader

CatalogEntryKind = Literal["tool", "skill", "workflow"]


class CatalogServer(BaseModel):
    """Browser projection of one verified live server registration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str
    name: str
    url: str
    status: Literal["available", "unavailable"]
    tool_count: int
    error: str | None = None


class CatalogEntry(BaseModel):
    """Browser projection of one catalog-owned current entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    kind: CatalogEntryKind
    description: str
    status: CatalogStatus
    authority: Literal["agent_component", "workflow_catalog"]
    server_name: str | None = None
    revision: int | None = None
    definition_digest: str | None = None
    content_digest: str | None = None


class CatalogCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: int
    tools: int
    skills: int
    workflows: int


class EnhancedCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["epistemic_graph"] = "epistemic_graph"
    servers: tuple[CatalogServer, ...]
    components: tuple[CatalogEntry, ...]
    counts: CatalogCounts


class CapabilityItem(BaseModel):
    """Existing workflow-palette item shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    kind: Literal["agent", "tool", "skill", "step", "team", "router"]
    system_prompt: str | None = None
    tools: tuple[str, ...] | None = None
    description: str | None = None


class WorkflowCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[CapabilityItem, ...]
    tools: tuple[CapabilityItem, ...]
    skills: tuple[CapabilityItem, ...]


class EnhancedCatalogAuthority:
    """Join complete, verified catalog snapshots from their owning domains."""

    def __init__(
        self,
        *,
        fleet: FleetCatalogReader,
        workflows: WorkflowCatalogReadPort,
        agents: AgentCatalogReadPort,
    ) -> None:
        self._fleet = fleet
        self._workflows = workflows
        self._agents = agents

    async def read(self) -> tuple[EnhancedCatalog, WorkflowCapabilities]:
        fleet = await self._fleet.read(kinds=("mcp_server", "tool", "skill"))
        workflows = tuple(await self._workflows.list_current_workflows())
        agents = tuple(await self._agents.list_authorized_agents())
        catalog = self._catalog(fleet, workflows)
        capabilities = WorkflowCapabilities(
            agents=tuple(
                CapabilityItem(
                    id=item.agent_id,
                    name=item.name,
                    kind="agent",
                    description=item.description,
                    system_prompt=item.system_prompt,
                    tools=item.tools,
                )
                for item in sorted(agents, key=lambda row: (row.name, row.agent_id))
            ),
            tools=self._capabilities(catalog, "tool"),
            skills=self._capabilities(catalog, "skill"),
        )
        return catalog, capabilities

    @staticmethod
    def _catalog(
        fleet: FleetCatalog, workflows: tuple[WorkflowCatalogRecord, ...]
    ) -> EnhancedCatalog:
        servers: list[CatalogServer] = []
        entries: list[CatalogEntry] = []
        seen_workflows: set[str] = set()
        for server in fleet.servers:
            provided = [
                item for item in server.provides if item.entry.kind in {"tool", "skill"}
            ]
            registration = server.registration
            servers.append(
                CatalogServer(
                    server_id=(
                        registration.server_id
                        if registration is not None
                        else f"srv:{server.component.server_name}"
                    ),
                    name=server.component.server_name,
                    url="" if registration is None else registration.url,
                    status=("unavailable" if registration is None else "available"),
                    tool_count=sum(item.entry.kind == "tool" for item in provided),
                    error=(
                        "live registration unavailable"
                        if registration is None
                        else None
                    ),
                )
            )
            entries.extend(
                CatalogEntry(
                    id=item.entry.component_id,
                    name=item.entry.upstream_name,
                    kind=cast(CatalogEntryKind, item.entry.kind),
                    description=item.entry.summary,
                    status="active",
                    authority="agent_component",
                    server_name=item.entry.server_name,
                    revision=item.entry.entry_revision,
                    definition_digest=item.entry.definition_digest,
                    content_digest=item.entry.content_digest,
                )
                for item in provided
            )
        for workflow in workflows:
            if workflow.workflow_id in seen_workflows:
                raise RuntimeError(
                    "workflow catalog returned a duplicate current identity"
                )
            seen_workflows.add(workflow.workflow_id)
            entries.append(
                CatalogEntry(
                    id=workflow.workflow_id,
                    name=workflow.name,
                    kind="workflow",
                    description=workflow.description,
                    status=workflow.status,
                    authority="workflow_catalog",
                    revision=workflow.revision,
                    definition_digest=workflow.definition_digest,
                )
            )
        entries.sort(key=lambda item: (item.kind, item.name, item.id))
        servers.sort(key=lambda item: (item.name, item.server_id))
        return EnhancedCatalog(
            servers=tuple(servers),
            components=tuple(entries),
            counts=CatalogCounts(
                servers=len(servers),
                tools=sum(item.kind == "tool" for item in entries),
                skills=sum(item.kind == "skill" for item in entries),
                workflows=len(workflows),
            ),
        )

    @staticmethod
    def _capabilities(
        catalog: EnhancedCatalog, kind: Literal["tool", "skill"]
    ) -> tuple[CapabilityItem, ...]:
        return tuple(
            CapabilityItem(
                id=item.id,
                name=item.name,
                kind=kind,
                description=item.description,
            )
            for item in catalog.components
            if item.kind == kind and item.status == "active"
        )


CatalogReader = Callable[[], Awaitable[tuple[EnhancedCatalog, WorkflowCapabilities]]]
_reader: CatalogReader | None = None


def configure_enhanced_catalog(authority: EnhancedCatalogAuthority) -> None:
    """Install the authority once at the GraphOS composition root."""

    global _reader
    _reader = authority.read


async def _read_catalog() -> tuple[EnhancedCatalog, WorkflowCapabilities]:
    if _reader is None:
        raise HTTPException(
            status_code=503, detail="catalog authority is not configured"
        )
    try:
        return await _reader()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="catalog authority unavailable"
        ) from exc


def register_enhanced_catalog_routes(app, *, prefix: str = "/api") -> None:
    """Mount the W6 browser contract with no WebUI or AU gateway dependency."""

    router = APIRouter(prefix=f"{prefix}/enhanced", tags=["catalog"])

    @router.get("/tools", response_model=EnhancedCatalog)
    async def tools() -> EnhancedCatalog:
        catalog, _ = await _read_catalog()
        return catalog

    @router.get("/skills", response_model=list[CatalogEntry])
    async def skills() -> list[CatalogEntry]:
        catalog, _ = await _read_catalog()
        return [item for item in catalog.components if item.kind == "skill"]

    @router.get("/workflows/capabilities", response_model=WorkflowCapabilities)
    async def workflow_capabilities() -> WorkflowCapabilities:
        _, capabilities = await _read_catalog()
        return capabilities

    app.include_router(router)


__all__ = [
    "AgentCatalogReadPort",
    "AgentCatalogRecord",
    "CatalogEntry",
    "CatalogServer",
    "EnhancedCatalog",
    "EnhancedCatalogAuthority",
    "WorkflowCatalogReadPort",
    "WorkflowCatalogRecord",
    "configure_enhanced_catalog",
    "register_enhanced_catalog_routes",
]
