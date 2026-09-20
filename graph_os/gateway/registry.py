"""Widget Registry — auto-discovers and manages service widgets.

CONCEPT:AU-OS.config.gateway-service-dashboard — Gateway Service Dashboard

Mirrors Homepage's ``widgets.js`` / ``components.js`` registry pattern
but uses Python's import mechanism for discovery. Now lives inside
``agent-utilities`` rather than the former standalone package.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from graph_os.gateway.models import WidgetRegistration

if TYPE_CHECKING:
    from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)

# Map of widget_type -> module path within graph_os.gateway.widgets
_BUILTIN_WIDGETS: dict[str, str] = {
    "portainer": "graph_os.gateway.widgets.portainer",
    "uptime_kuma": "graph_os.gateway.widgets.uptime_kuma",
    "technitium": "graph_os.gateway.widgets.technitium",
    "caddy": "graph_os.gateway.widgets.caddy",
    "gitlab": "graph_os.gateway.widgets.gitlab",
    "jellyfin": "graph_os.gateway.widgets.jellyfin",
    "qbittorrent": "graph_os.gateway.widgets.qbittorrent",
    "nextcloud": "graph_os.gateway.widgets.nextcloud",
    "home_assistant": "graph_os.gateway.widgets.home_assistant",
    "mealie": "graph_os.gateway.widgets.mealie",
    "container_manager": "graph_os.gateway.widgets.container_manager",
    "mattermost": "graph_os.gateway.widgets.mattermost",
    "keycloak": "graph_os.gateway.widgets.keycloak",
    "openbao": "graph_os.gateway.widgets.openbao",
    "langfuse": "graph_os.gateway.widgets.langfuse",
    "plane": "graph_os.gateway.widgets.plane",
    "servicenow": "graph_os.gateway.widgets.servicenow",
    "erpnext": "graph_os.gateway.widgets.erpnext",
    "wger": "graph_os.gateway.widgets.wger",
    "owncast": "graph_os.gateway.widgets.owncast",
    "github": "graph_os.gateway.widgets.github",
    "searxng": "graph_os.gateway.widgets.searxng",
    "media_downloader": "graph_os.gateway.widgets.media_downloader",
    "stirlingpdf": "graph_os.gateway.widgets.stirlingpdf",
    "microsoft": "graph_os.gateway.widgets.microsoft",
    "postiz": "graph_os.gateway.widgets.postiz",
    "archivebox": "graph_os.gateway.widgets.archivebox",
    "leanix": "graph_os.gateway.widgets.leanix",
    "listmonk": "graph_os.gateway.widgets.listmonk",
    "tunnel_manager": "graph_os.gateway.widgets.tunnel_manager",
    "repository_manager": "graph_os.gateway.widgets.repository_manager",
    "systems_manager": "graph_os.gateway.widgets.systems_manager",
    "data_science": "graph_os.gateway.widgets.data_science",
    "vector_db": "graph_os.gateway.widgets.vector_db",
    "documentdb": "graph_os.gateway.widgets.documentdb",
    "scholarx": "graph_os.gateway.widgets.scholarx",
    "audio_transcriber": "graph_os.gateway.widgets.audio_transcriber",
    "arr": "graph_os.gateway.widgets.arr",
    "ansible_tower": "graph_os.gateway.widgets.ansible_tower",
    "lgtm": "graph_os.gateway.widgets.lgtm",
    "emerald_exchange": "graph_os.gateway.widgets.emerald_exchange",
    "legal_peripherals": "graph_os.gateway.widgets.legal_peripherals",
    "twenty": "graph_os.gateway.widgets.twenty",
    "orchestrator": "graph_os.gateway.widgets.orchestrator",
    "atlassian": "graph_os.gateway.widgets.atlassian",
    "google_workspace": "graph_os.gateway.widgets.google_workspace",
    "zulip": "graph_os.gateway.widgets.zulip",
    "teleport": "graph_os.gateway.widgets.teleport",
    "sentry": "graph_os.gateway.widgets.sentry",
    "ollama": "graph_os.gateway.widgets.ollama",
    # Connector-fleet expansion (widget-connector-expansion) — one entry per
    # agent-packages/agents/* connector that exposes a usable programmatic
    # client. See
    # docs/pillars/5_agent_os_infrastructure/OS-5.9-Gateway_Service_Dashboard.md
    # for the full connector<->widget inventory and the connectors excluded
    # (no single-service status to poll, or no simple url+token client shape).
    "aris": "graph_os.gateway.widgets.aris",
    "audiobookshelf": "graph_os.gateway.widgets.audiobookshelf",
    "camunda": "graph_os.gateway.widgets.camunda",
    "ciso_assistant": "graph_os.gateway.widgets.ciso_assistant",
    "clarity": "graph_os.gateway.widgets.clarity",
    "dockerhub": "graph_os.gateway.widgets.dockerhub",
    "egeria": "graph_os.gateway.widgets.egeria",
    "fan_manager": "graph_os.gateway.widgets.fan_manager",
    "firefly_iii": "graph_os.gateway.widgets.firefly_iii",
    "freshrss": "graph_os.gateway.widgets.freshrss",
    "gramps": "graph_os.gateway.widgets.gramps",
    "hdhomerun": "graph_os.gateway.widgets.hdhomerun",
    "jena": "graph_os.gateway.widgets.jena",
    "kafka": "graph_os.gateway.widgets.kafka",
    "okta": "graph_os.gateway.widgets.okta",
    "onetrust": "graph_os.gateway.widgets.onetrust",
    "paperless_ngx": "graph_os.gateway.widgets.paperless_ngx",
    "pulselink": "graph_os.gateway.widgets.pulselink",
    "rom_manager": "graph_os.gateway.widgets.rom_manager",
}


class Registry:
    """Global registry of available service widgets.

    Provides lazy loading — widgets are only imported when first accessed,
    so frontends don't pay import cost for unused agent-packages.
    """

    def __init__(self) -> None:
        self._widgets: dict[str, type[BaseWidget]] = {}
        self._registrations: dict[str, WidgetRegistration] = {}
        self._loaded: set[str] = set()

    def discover_all(self) -> dict[str, WidgetRegistration]:
        """Attempt to load all builtin widgets and return their registrations.

        Widgets whose agent-package dependencies aren't installed are
        silently skipped (graceful degradation).
        """
        for widget_type, module_path in _BUILTIN_WIDGETS.items():
            if widget_type not in self._loaded:
                self._try_load(widget_type, module_path)
        return dict(self._registrations)

    def get_widget(self, widget_type: str) -> BaseWidget | None:
        """Get an instantiated widget by type key.

        Lazily imports the widget module on first access.
        """
        if widget_type not in self._widgets:
            module_path = _BUILTIN_WIDGETS.get(widget_type)
            if not module_path:
                logger.warning("Unknown widget type: %s", widget_type)
                return None
            self._try_load(widget_type, module_path)

        widget_cls = self._widgets.get(widget_type)
        if widget_cls:
            return widget_cls()
        return None

    def get_registration(self, widget_type: str) -> WidgetRegistration | None:
        """Get widget registration metadata without instantiating."""
        if widget_type not in self._registrations:
            self.get_widget(widget_type)  # Trigger lazy load
        return self._registrations.get(widget_type)

    def list_available(self) -> list[str]:
        """List all widget types that can be loaded (deps installed)."""
        self.discover_all()
        return list(self._registrations.keys())

    def list_all_known(self) -> list[str]:
        """List all known widget types (including those with missing deps)."""
        return list(_BUILTIN_WIDGETS.keys())

    def _try_load(self, widget_type: str, module_path: str) -> None:
        """Attempt to import a widget module. Silently skip on ImportError."""
        self._loaded.add(widget_type)
        try:
            module = importlib.import_module(module_path)
            widget_cls = getattr(module, "Widget", None)
            if widget_cls is None:
                logger.debug(
                    "Widget module %s has no 'Widget' class, skipping", module_path
                )
                return

            self._widgets[widget_type] = widget_cls

            # Build registration from widget class attributes
            instance = widget_cls()
            self._registrations[widget_type] = WidgetRegistration(
                widget_type=instance.service_type,
                display_name=instance.display_name,
                icon=instance.icon,
                category=instance.category,
                description=instance.description,
                available_fields=instance.get_fields(),
                supports_websocket=getattr(instance, "supports_websocket", False),
                env_prefix=getattr(instance, "env_prefix", ""),
            )
            logger.debug("Loaded widget: %s (%s)", widget_type, instance.display_name)

        except ImportError as exc:
            logger.warning("Widget %s skipped: %s", widget_type, exc)
        except Exception as exc:
            logger.warning(
                "Widget %s failed to load (exception_type=%s)",
                widget_type,
                exc,
            )


# Global singleton
_registry: Registry | None = None


def get_registry() -> Registry:
    """Get or create the global widget registry singleton."""
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
