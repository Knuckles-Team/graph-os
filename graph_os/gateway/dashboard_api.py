"""Service dashboard REST and WebSocket surface (GRAPHOS-HOST-R019).

CONCEPT:AU-OS.config.gateway-service-dashboard — Gateway Service Dashboard

agent-webui commit ``08ab4ea`` removed its own ``/api/dashboard`` mount and the
``/ws/dashboard`` stream so GraphOS could own them through the application
composer. GraphOS never registered them, so the browser received 404 for the
REST reads and a bare 403 for the unmatched WebSocket handshake.

This module ports the agent-utilities ``gateway/api.py`` dashboard router and
the agent-webui WebSocket handler onto GraphOS's own gateway aggregator,
registry and daemon. :func:`register_dashboard_routes` mounts both from
:func:`graph_os.gateway.graph_api.register_graph_routes`, so the routes share
the identity middleware and the WebUI authorization middleware that guard
every other ``/api`` route. The WebSocket handler performs no extra auth
check; the middleware chain decides who may connect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Annotated, Any

from agent_utilities.security.error_surface import public_error_payload
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from graph_os.gateway.aggregator import Aggregator
from graph_os.gateway.config import ConfigManager
from graph_os.gateway.models import DashboardLayout, WidgetData
from graph_os.gateway.registry import get_registry

__all__ = [
    "DASHBOARD_WS_PATH",
    "DashboardResponse",
    "dashboard_router",
    "fetch_dashboard_subset",
    "get_full_dashboard",
    "register_dashboard_routes",
]

logger = logging.getLogger(__name__)

DASHBOARD_WS_PATH = "/ws/dashboard"
_SERVICE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HYDRATION_SOURCE_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")
#: Bound on how many widget ids one ``subscribe`` message may name.
_MAX_SUBSCRIBE_WIDGET_IDS = 256
#: Push cadence after the initial snapshot. Matches the aggregator read cache.
_WS_PUSH_INTERVAL_SECONDS = 15.0
_READ_CAPABILITIES = frozenset(
    {"gateway:read", "gateway:write", "gateway:admin", "admin"}
)
_WRITE_CAPABILITIES = frozenset({"gateway:write", "gateway:admin", "admin"})


def _dashboard_capabilities(request: Request) -> set[str] | None:
    claims = getattr(request.state, "user_claims", None)
    if not claims or claims.get("auth_type") == "api_key":
        return None
    try:
        from agent_utilities.core.config import config
        from agent_utilities.security.identity import (
            base_capabilities,
            normalize_identity,
        )

        return set(
            base_capabilities(
                normalize_identity(claims), config.identity_group_capability_map
            )
        )
    except Exception:
        raise HTTPException(
            status_code=403, detail="dashboard capability required"
        ) from None


async def _require_dashboard_read(request: Request) -> None:
    if request.url.path.endswith("/health"):
        return
    capabilities = _dashboard_capabilities(request)
    if capabilities is not None and not capabilities & _READ_CAPABILITIES:
        raise HTTPException(status_code=403, detail="dashboard capability required")


def _require_dashboard_write(request: Request) -> None:
    capabilities = _dashboard_capabilities(request)
    if capabilities is not None and not capabilities & _WRITE_CAPABILITIES:
        raise HTTPException(
            status_code=403, detail="dashboard write capability required"
        )


dashboard_router = APIRouter(
    tags=["dashboard"], dependencies=[Depends(_require_dashboard_read)]
)

# Per-process singleton, created on first request. Layout writes persist to the
# shared YAML file, so replicas converge on their next read.
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
    """Return the current dashboard layout configuration."""
    return _get_aggregator().get_layout()


@dashboard_router.put("/layout")
async def save_layout(layout: DashboardLayout, request: Request) -> dict[str, str]:
    """Persist a new dashboard layout configuration."""
    _require_dashboard_write(request)
    _get_aggregator().save_layout(layout)
    return {"status": "saved"}


@dashboard_router.get("/data")
async def get_all_data() -> dict[str, WidgetData]:
    """Fetch data from every active widget."""
    return await _get_aggregator().fetch_all()


@dashboard_router.get("/data/{service_id}")
async def get_service_data(service_id: str) -> WidgetData:
    """Fetch data for one service."""
    if not _SERVICE_ID_RE.fullmatch(service_id):
        raise HTTPException(status_code=404, detail="service not found")
    data = await _get_aggregator().fetch_one(service_id)
    if data.status == "error" and "not found" in (data.error or ""):
        raise HTTPException(status_code=404, detail="service not found")
    return data


@dashboard_router.get("/full")
async def get_full_dashboard() -> DashboardResponse:
    """Return layout and data in one request for the initial page load."""
    aggregator = _get_aggregator()
    layout = aggregator.get_layout()
    data = await aggregator.fetch_all()
    return DashboardResponse(layout=layout, data=data)


async def fetch_dashboard_subset(widget_ids: set[str]) -> dict[str, WidgetData]:
    """Fetch exactly the named widgets concurrently through ``fetch_one``."""
    aggregator = _get_aggregator()
    ordered_ids = list(widget_ids)
    results = await asyncio.gather(
        *(aggregator.fetch_one(widget_id) for widget_id in ordered_ids)
    )
    return dict(zip(ordered_ids, results, strict=True))


@dashboard_router.get("/data-subset")
async def get_dashboard_subset(
    widget_id: Annotated[list[str] | None, Query()] = None,
) -> dict[str, WidgetData]:
    """REST twin of :func:`fetch_dashboard_subset`."""
    return await fetch_dashboard_subset(set(widget_id or ()))


@dashboard_router.get("/widgets")
async def list_available_widgets() -> list[WidgetListItem]:
    """List every widget type available for configuration."""
    registrations = get_registry().discover_all()
    return [
        WidgetListItem(
            widget_type=r.widget_type,
            display_name=r.display_name,
            icon=r.icon,
            category=r.category.value,
            description=r.description,
            supports_websocket=r.supports_websocket,
        )
        for r in registrations.values()
    ]


@dashboard_router.get("/health")
async def health_check() -> JSONResponse:
    """Liveness: always 200, with the shared runtime health report as body."""
    from agent_utilities.observability.runtime_health import collect_health_async

    report = await collect_health_async()
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


@dashboard_router.get("/discover")
async def discover_services() -> DashboardLayout:
    """Auto-discover services from the MCP configuration as a layout."""
    return ConfigManager()._auto_discover()


@dashboard_router.get("/daemon/status")
async def daemon_status() -> dict[str, Any]:
    """Return the consolidated KG host daemon status."""
    from graph_os.gateway.daemon import daemon_status as _status

    return _status()


@dashboard_router.get("/daemon/shards")
async def daemon_shards() -> dict[str, Any]:
    """Return the engine shard topology and per-shard reachability."""
    from agent_utilities.knowledge_graph.core.shard_topology import (
        shard_topology_status,
    )

    return shard_topology_status()


@dashboard_router.post("/daemon/start")
async def daemon_start(request: Request) -> dict[str, Any]:
    """Ensure the consolidated KG host daemon runs in this process."""
    _require_dashboard_write(request)
    from graph_os.gateway.daemon import daemon_status as _status
    from graph_os.gateway.daemon import start_host_daemon

    start_host_daemon()
    return _status()


def _active_engine() -> Any:
    from agent_utilities.knowledge_graph.core.engine import IntelligenceGraphEngine

    engine = IntelligenceGraphEngine.get_active()
    if not engine:
        raise HTTPException(
            status_code=500, detail="Active Knowledge Graph engine not available"
        )
    return engine


def _hydration_manager() -> Any:
    from agent_utilities.knowledge_graph.core.hydration import HydrationManager

    return HydrationManager()


@dashboard_router.post("/hydrate/{source}")
async def trigger_hydration(source: str, request: Request) -> dict[str, Any]:
    """Trigger hydration for one external source."""
    _require_dashboard_write(request)
    if not _HYDRATION_SOURCE_RE.fullmatch(source):
        raise HTTPException(status_code=422, detail="invalid hydration source")
    engine = _active_engine()
    try:
        return _hydration_manager().hydrate_source(engine, source)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=public_error_payload(exc, logger=logger, code="invalid_request"),
        ) from None
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=public_error_payload(exc, logger=logger)
        ) from None


@dashboard_router.post("/hydrate")
async def trigger_all_hydration(request: Request) -> dict[str, Any]:
    """Trigger hydration for every configured source in sequence."""
    _require_dashboard_write(request)
    engine = _active_engine()
    try:
        return _hydration_manager().hydrate_all(engine)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=public_error_payload(exc, logger=logger)
        ) from None


@dashboard_router.get("/hydration-status")
async def get_hydration_status() -> dict[str, Any]:
    """Return the configuration status of every hydration source."""
    return _hydration_manager().get_status()


def parsed_widget_subscription(raw: str) -> set[str] | None:
    """Return the widget ids a ``subscribe`` message names, else ``None``."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("type") != "subscribe":
        return None
    widget_ids = parsed.get("widget_ids")
    if not isinstance(widget_ids, list):
        return None
    return {
        widget_id
        for widget_id in widget_ids[:_MAX_SUBSCRIBE_WIDGET_IDS]
        if isinstance(widget_id, str)
    }


async def _widgets_for(subscription: set[str] | None) -> dict[str, WidgetData]:
    if subscription is None:
        return (await get_full_dashboard()).data
    return await fetch_dashboard_subset(subscription)


async def _next_subscription(
    websocket: WebSocket, current: set[str] | None
) -> set[str] | None:
    """Wait one push interval for a ``subscribe`` message."""
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=_WS_PUSH_INTERVAL_SECONDS
        )
    except TimeoutError:
        return current
    subscription = parsed_widget_subscription(raw)
    return current if subscription is None else subscription


async def _stream_dashboard(websocket: WebSocket) -> None:
    """Send a snapshot, then periodic updates shaped like ``/full``'s data.

    Each message carries a per-connection ``stream_id`` and a monotonic
    ``sequence``. A new ``stream_id`` tells a client to treat the snapshot as a
    reset. A ``subscribe`` message scopes later pushes to the named widgets.
    """
    stream_id = uuid.uuid4().hex
    sequence = 0
    message_type = "snapshot"
    subscription: set[str] | None = None
    while True:
        widgets = await _widgets_for(subscription)
        sequence += 1
        await websocket.send_json(
            {
                "type": message_type,
                "stream_id": stream_id,
                "sequence": sequence,
                "data": {
                    widget_id: widget.model_dump(mode="json")
                    for widget_id, widget in widgets.items()
                },
            }
        )
        message_type = "update"
        subscription = await _next_subscription(websocket, subscription)


async def dashboard_websocket(websocket: WebSocket) -> None:
    """``/ws/dashboard``: stream dashboard widget data to the browser."""
    await websocket.accept()
    try:
        await _stream_dashboard(websocket)
    except WebSocketDisconnect as exc:
        logger.info("/ws/dashboard closed by client: code=%s", exc.code)
    except Exception:
        logger.warning("/ws/dashboard stream failed", exc_info=True)


def register_dashboard_routes(app: Any, *, prefix: str = "/api") -> None:
    """Mount ``{prefix}/dashboard/*`` and ``/ws/dashboard`` onto ``app``."""
    app.include_router(dashboard_router, prefix=f"{prefix}/dashboard")
    if not any(getattr(r, "path", None) == DASHBOARD_WS_PATH for r in app.routes):
        app.add_api_websocket_route(DASHBOARD_WS_PATH, dashboard_websocket)
    logger.info("Mounted service dashboard API at %s/dashboard", prefix)
