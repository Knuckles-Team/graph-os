"""Dashboard REST projection served by graph-os.

The widget registry, configuration, and aggregator already live in this package.
This router exposes them under the same paths formerly served by AU while the
legacy AU gateway is retired by EH-476.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from graph_os.gateway.aggregator import Aggregator
from graph_os.gateway.config import ConfigManager
from graph_os.gateway.models import DashboardLayout, WidgetData
from graph_os.gateway.registry import get_registry

_SERVICE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _require_read() -> None:
    _require_scope("kg:read")


def _require_write() -> None:
    _require_scope("kg:write")


def _require_scope(scope: str) -> None:
    from agent_utilities.api.session import (
        ScopeError,
        SessionRequiredError,
        resolve_session,
    )

    try:
        resolve_session(required_scope=scope)
    except ScopeError:
        raise HTTPException(status_code=403, detail="dashboard access denied") from None
    except SessionRequiredError:
        raise HTTPException(status_code=401, detail="session required") from None


dashboard_router = APIRouter(tags=["dashboard"], dependencies=[Depends(_require_read)])
_aggregator: Aggregator | None = None


def _get_aggregator() -> Aggregator:
    global _aggregator
    if _aggregator is None:
        _aggregator = Aggregator(config_manager=ConfigManager())
    return _aggregator


class DashboardResponse(BaseModel):
    layout: DashboardLayout
    data: dict[str, WidgetData]


class WidgetListItem(BaseModel):
    widget_type: str
    display_name: str
    icon: str
    category: str
    description: str
    supports_websocket: bool


@dashboard_router.get("/layout")
async def get_layout() -> DashboardLayout:
    return _get_aggregator().get_layout()


@dashboard_router.put("/layout", dependencies=[Depends(_require_write)])
async def save_layout(layout: DashboardLayout) -> dict[str, str]:
    _get_aggregator().save_layout(layout)
    return {"status": "saved"}


@dashboard_router.get("/data")
async def get_all_data() -> dict[str, WidgetData]:
    return await _get_aggregator().fetch_all()


async def fetch_dashboard_subset(widget_ids: set[str]) -> dict[str, WidgetData]:
    """Poll only subscribed widgets, without computing the full dashboard."""
    ordered = sorted(widget_ids)
    values = await asyncio.gather(
        *(_get_aggregator().fetch_one(widget_id) for widget_id in ordered)
    )
    return dict(zip(ordered, values, strict=True))


@dashboard_router.get("/data-subset")
async def get_dashboard_subset(
    widget_id: Annotated[list[str] | None, Query()] = None,
) -> dict[str, WidgetData]:
    return await fetch_dashboard_subset(set(widget_id or ()))


@dashboard_router.get("/data/{service_id}")
async def get_service_data(service_id: str) -> WidgetData:
    if not _SERVICE_ID_RE.fullmatch(service_id):
        raise HTTPException(status_code=404, detail="service not found")
    data = await _get_aggregator().fetch_one(service_id)
    if data.status == "error" and "not found" in (data.error or ""):
        raise HTTPException(status_code=404, detail="service not found")
    return data


@dashboard_router.get("/full")
async def get_full_dashboard() -> DashboardResponse:
    aggregator = _get_aggregator()
    return DashboardResponse(
        layout=aggregator.get_layout(), data=await aggregator.fetch_all()
    )


@dashboard_router.get("/widgets")
async def list_available_widgets() -> list[WidgetListItem]:
    registrations = get_registry().discover_all()
    return [
        WidgetListItem(
            widget_type=item.widget_type,
            display_name=item.display_name,
            icon=item.icon,
            category=item.category.value,
            description=item.description,
            supports_websocket=item.supports_websocket,
        )
        for item in registrations.values()
    ]


@dashboard_router.get("/discover")
async def discover_services() -> DashboardLayout:
    return ConfigManager()._auto_discover()


def register_dashboard_routes(app, prefix: str = "/api") -> None:
    """Mount dashboard endpoints once; all routes share the gateway session."""
    base = f"{prefix}/dashboard"
    if any(getattr(route, "path", None) == f"{base}/layout" for route in app.routes):
        return
    app.include_router(dashboard_router, prefix=base)
