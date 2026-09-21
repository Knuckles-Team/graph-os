"""Wger widget — fitness and workout tracking."""

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
    service_type = "wger"
    display_name = "Wger"
    icon = "dumbbell"
    category = ServiceCategory.LIFESTYLE
    description = "Fitness tracker — workouts, exercises, and body measurements"
    env_prefix = "WGER"

    def get_fields(self) -> list[WidgetField]:
        return [
            WidgetField(key="workouts", label="Workouts", format="number"),
            WidgetField(key="exercises", label="Exercises", format="number"),
            WidgetField(key="routines", label="Routines", format="number"),
        ]

    def fetch_data(self, config: ServiceConfig) -> WidgetData:
        client = self._fleet_client()

        try:
            workouts = client.get_workouts() or []
            exercises = client.get_exercises() or []
        except Exception as e:
            logger.warning("Wger fetch: %s", e)
            return self._error_data(e)

        return WidgetData(
            fields={
                "workouts": len(workouts) if isinstance(workouts, list) else 0,
                "exercises": len(exercises) if isinstance(exercises, list) else 0,
                "routines": 0,
            },
            status="ok",
        )
