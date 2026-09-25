"""Vector DB widget — vector database collections and embedding status."""

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


class Widget(BaseWidget):
    service_type = "vector_db"
    display_name = "Vector DB"
    icon = "database"
    category = ServiceCategory.DATA_SCIENCE
    description = "Vector database — collections, embeddings, and similarity search"
    env_prefix = "VECTOR"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="collections", label="Collections", format="number"),
            WidgetField(key="points", label="Points", format="number"),
            WidgetField(key="status", label="Status", format="text"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        try:
            result = self._fleet_client().vector_collection_management(
                action="list_collections"
            )
        except Exception as e:
            logger.warning("Vector DB fetch: %s", e)
            return self._error_data(e)

        collections = result.get("collections", []) if isinstance(result, dict) else []
        return WidgetData(
            fields={"collections": len(collections), "points": 0, "status": "Online"},
            status="ok",
        )
