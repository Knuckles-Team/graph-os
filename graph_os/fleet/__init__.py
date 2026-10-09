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
from .error_budget import (
    AimdConfig,
    BudgetWindow,
    OutcomeClass,
    OutcomeSample,
    Partition,
    ThrottleDecision,
    ThrottleMode,
    decide,
)
from .multiplexer import (
    MCPMultiplexer,
    attach_fleet_loader,
    auto_server_prefix,
    clean_tool_name,
    get_server_prefix,
)

__all__ = [
    "FLEET_COMPONENT_KINDS",
    "AimdConfig",
    "BudgetWindow",
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
    "OutcomeClass",
    "OutcomeSample",
    "Partition",
    "ReadContext",
    "ReadReceipt",
    "ServerPage",
    "ServerRegistration",
    "ThrottleDecision",
    "ThrottleMode",
    "attach_fleet_loader",
    "auto_server_prefix",
    "clean_tool_name",
    "decide",
    "get_server_prefix",
]
