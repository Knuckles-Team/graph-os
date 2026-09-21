"""Mealie widget — recipe management and meal planning status."""

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
    service_type = "mealie"
    display_name = "Mealie"
    icon = "utensils"
    category = ServiceCategory.LIFESTYLE
    description = "Recipe manager — recipes, meal plans, and shopping lists"
    env_prefix = "MEALIE"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="recipes", label="Recipes", format="number"),
            WidgetField(key="categories", label="Categories", format="number"),
            WidgetField(key="tags", label="Tags", format="number"),
            WidgetField(key="meal_plans", label="Meal Plans", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            recipes = client.get_recipes(page=1, per_page=1) or {}
            categories = client.get_categories() or []
            tags = client.get_tags() or []
            total_recipes = (
                recipes.get("total", 0) if isinstance(recipes, dict) else len(recipes)
            )
        except Exception as e:
            logger.warning("Mealie fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "recipes": total_recipes,
                "categories": len(categories) if isinstance(categories, list) else 0,
                "tags": len(tags) if isinstance(tags, list) else 0,
                "meal_plans": 0,
            },
            status="ok",
        )
