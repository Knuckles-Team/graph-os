"""Verified dashboard health and hydration routes on the current gateway."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import JSONResponse

_HYDRATION_SOURCE_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


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


def _require_read() -> None:
    _require_scope("kg:read")


def _require_write() -> None:
    _require_scope("kg:write")


def _require_admin() -> None:
    _require_scope("kg:admin")


dashboard_router = APIRouter(tags=["dashboard"], dependencies=[Depends(_require_read)])


@dashboard_router.get("/health")
async def health_check() -> JSONResponse:
    from agent_utilities.observability.runtime_health import collect_health_async

    return JSONResponse(
        await collect_health_async(), headers={"Cache-Control": "no-store"}
    )


@dashboard_router.get("/daemon/status")
async def daemon_status() -> dict[str, Any]:
    from graph_os.gateway.daemon import daemon_status as current_status

    return current_status()


@dashboard_router.get("/daemon/shards")
async def daemon_shards() -> dict[str, Any]:
    from agent_utilities.knowledge_graph.core.shard_topology import (
        shard_topology_status,
    )

    return shard_topology_status()


@dashboard_router.post("/daemon/start", dependencies=[Depends(_require_write)])
async def daemon_start() -> dict[str, Any]:
    from graph_os.gateway.daemon import daemon_status as current_status
    from graph_os.gateway.daemon import start_host_daemon

    start_host_daemon()
    return current_status()


def _hydration_engine() -> Any:
    from graph_os.gateway.ports import gateway_application

    try:
        engine = gateway_application().engine()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="hydration unavailable") from exc
    if engine is None:
        raise HTTPException(status_code=503, detail="hydration unavailable")
    return engine


@dashboard_router.post("/hydrate/{source}", dependencies=[Depends(_require_admin)])
async def trigger_hydration(source: str) -> dict[str, Any]:
    if not _HYDRATION_SOURCE_RE.fullmatch(source):
        raise HTTPException(status_code=422, detail="invalid hydration source")
    from agent_utilities.api.hydration import hydrate_source

    try:
        return await asyncio.to_thread(hydrate_source, _hydration_engine(), source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid hydration source") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="hydration unavailable") from exc


@dashboard_router.post("/hydrate", dependencies=[Depends(_require_admin)])
async def trigger_all_hydration() -> dict[str, Any]:
    from agent_utilities.api.hydration import hydrate_all

    try:
        return await asyncio.to_thread(hydrate_all, _hydration_engine())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="hydration unavailable") from exc


@dashboard_router.get("/hydration-status")
async def get_hydration_status() -> dict[str, Any]:
    from agent_utilities.api.hydration import hydration_status

    try:
        return await asyncio.to_thread(hydration_status)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="hydration unavailable") from exc


def register_dashboard_runtime_routes(app: Any, prefix: str = "/api") -> None:
    """Mount only the live runtime routes; legacy dashboard catalogs stay retired."""
    base = f"{prefix}/dashboard"
    if any(getattr(route, "path", None) == f"{base}/health" for route in app.routes):
        return
    app.include_router(dashboard_router, prefix=base)
