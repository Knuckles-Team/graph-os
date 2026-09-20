"""RF-ADR-009 gateway — REST host, dashboard, and service aggregation.

CONCEPT:AU-OS.config.gateway-service-dashboard — Gateway Service Dashboard

Provides the widget registry, data aggregation, and API layer that all
three frontends (agent-webui, agent-terminal-ui, geniusbot) use to render
Homepage-style service dashboards.

Replaces the former standalone ``service-dashboard-core`` package by folding
its genuinely new functionality (UI models, parallel aggregator, widget ABC)
into the graph-os transport host while application behavior remains behind
the explicit ports in :mod:`graph_os.gateway.ports`.

Usage::

    from graph_os.gateway import Aggregator, ConfigManager
    from graph_os.gateway.models import WidgetData, DashboardLayout
    from graph_os.gateway.registry import get_registry
"""

from graph_os._version import __version__ as __version__
from graph_os.gateway.models import (
    DashboardLayout,
    ServiceCategory,
    ServiceConfig,
    ServiceGroup,
    WidgetData,
    WidgetField,
    WidgetRegistration,
)
from graph_os.gateway.registry import Registry, get_registry

__all__ = [
    "Aggregator",
    "ConfigManager",
    "DashboardLayout",
    "Registry",
    "ServiceCategory",
    "ServiceConfig",
    "ServiceGroup",
    "WidgetData",
    "WidgetField",
    "WidgetRegistration",
    "get_registry",
]
