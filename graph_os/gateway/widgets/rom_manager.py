"""ROM Manager widget — RomM remote game-library server.

Targets the RomM *remote library server* client (``rom_manager.romm.api``),
not the local ROM-conversion facade (which wraps on-disk file operations and
has no reachable service to dashboard).
"""

from __future__ import annotations

import logging

from graph_os.gateway.models import (
    ServiceCategory,
    ServiceConfig,
    WidgetData,
    WidgetField,
)
from graph_os.gateway.widgets.base import BaseWidget

logger = logging.getLogger(__name__)


def _stat(stats: dict, *names: str) -> int:
    """Case-insensitive lookup across RomM's ``/api/stats`` key spelling."""
    lowered = {str(k).lower(): v for k, v in stats.items()}
    for name in names:
        value = lowered.get(name.lower())
        if isinstance(value, int):
            return value
    return 0


class Widget(BaseWidget):
    service_type = "rom_manager"
    display_name = "ROM Manager"
    icon = "gamepad-2"
    category = ServiceCategory.MEDIA
    description = "RomM game library — platforms and ROM counts"
    env_prefix = "ROM_MANAGER"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="platforms", label="Platforms", format="number"),
            WidgetField(key="roms", label="ROMs", format="number", highlight=True),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()
        try:
            stats = client.stats() or {}
        except Exception as e:
            return self._error_data(e)

        if not isinstance(stats, dict):
            stats = {}

        return WidgetData(
            fields={
                "platforms": _stat(stats, "PLATFORMS", "platforms"),
                "roms": _stat(stats, "ROMS", "roms"),
            },
            status="ok",
        )
