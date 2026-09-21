"""Fleet gateway (multiplexer) — RF-ADR-009 §2.4 Phase 5 target.

Owns the in-process fleet loader that lazily fronts every `*-mcp` service
(`find_tools` / `list_catalog` / `load_tools`). Source today:
the extracted :mod:`graph_os.fleet.multiplexer` (formerly
``agent_utilities/mcp/multiplexer.py``; see AGENTS.md "W5 source measurements").
Per RF-ADR-009 §2.4, the loader itself stays; its catalog moves from a static
file to the epistemic-graph server registry, and the multiplexer's skill/
prompt harvest path (`fleet_skill_harvest.py`, `fleet_prompt_harvest.py`) is
deleted once the agent-connector-sdk sync runner imports packs natively (W2,
§2.1 item 5). The first native seam is :mod:`graph_os.fleet.catalog_reader`,
an EG-only joined view over live server registrations and current components.
"""

from .catalog_reader import (
    FLEET_COMPONENT_KINDS,
    CatalogComponent,
    CatalogServer,
    ComponentContent,
    ComponentPage,
    ComponentPin,
    ComponentRecord,
    ComponentSearchRequest,
    CurrentComponent,
    FleetCatalog,
    FleetCatalogIntegrityError,
    FleetCatalogReader,
    FleetCatalogReadPort,
    ReadContext,
    ReadReceipt,
    ServerPage,
    ServerRegistration,
)
from .multiplexer import (
    MCPMultiplexer,
    SessionVisibilityMiddleware,
    attach_fleet_loader,
    auto_server_prefix,
    clean_tool_name,
    get_server_prefix,
)

__all__ = [
    "FLEET_COMPONENT_KINDS",
    "CatalogComponent",
    "CatalogServer",
    "ComponentContent",
    "ComponentPage",
    "ComponentPin",
    "ComponentRecord",
    "ComponentSearchRequest",
    "CurrentComponent",
    "FleetCatalog",
    "FleetCatalogIntegrityError",
    "FleetCatalogReadPort",
    "FleetCatalogReader",
    "MCPMultiplexer",
    "ReadContext",
    "ReadReceipt",
    "ServerPage",
    "ServerRegistration",
    "SessionVisibilityMiddleware",
    "attach_fleet_loader",
    "auto_server_prefix",
    "clean_tool_name",
    "get_server_prefix",
]
