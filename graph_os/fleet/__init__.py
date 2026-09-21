"""Fleet gateway and child lifecycle — RF-ADR-009 §2.4.

Owns the in-process fleet loader that lazily fronts every `*-mcp` service
(`find_tools` / `list_catalog` / `load_tools`). Child transport and session
visibility are live here. The EG-backed catalog reader is capability-gated
until the generated joined registry/component query is public; it refuses
rather than substituting the served static declaration as durable authority.
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
    DeferredFleetCatalogReader,
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
    "DeferredFleetCatalogReader",
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
