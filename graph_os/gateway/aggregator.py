"""Async Data Aggregator — parallel fetching across all active widgets.

CONCEPT:AU-OS.config.gateway-service-dashboard — Gateway Service Dashboard

Designed for all three frontends:
  - WebUI: Called by FastAPI endpoint, returns JSON
  - TUI: Called directly via ``async for data in aggregator.stream()``
  - GUI: Called via ``asyncio.run(aggregator.fetch_all())``
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from agent_utilities.security.error_surface import public_error_payload

from graph_os.gateway.config import ConfigManager
from graph_os.gateway.models import (
    DashboardLayout,
    ServiceConfig,
    WidgetData,
)
from graph_os.gateway.registry import Registry, get_registry

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# GRAPHOS-FLEET-R032: the service authority the aggregator binds around
# every widget fetch, lazily minted and cached process-wide (same shape as
# agent_webui.graph_admission's `_SERVICE_SESSION` cache). Guarded by
# `_SERVICE_SESSION_LOCK`, a plain non-reentrant lock held only for the
# short check-or-mint decision, never across the fetch itself.
_SERVICE_SESSION: Any | None = None
_SERVICE_SESSION_LOCK = threading.Lock()
_SERVICE_AUTHORITY_RENEWAL_MARGIN_SECONDS = 30


def _service_authority() -> Any:
    """Return the gateway's own verified process actor, minting once.

    Reuses the same process-identity authority
    ``graph_os/gateway/daemon.py::mint_process_identity`` already mints for
    the standalone host daemon (``acquire_process_identity_token`` ->
    ``mint_actor_from_token_sync`` -> ``mint_graph_session``) — this grants
    the aggregator nothing new, it is the credential this deployment already
    holds. Never borrows a caller's identity: a widget fetch has no caller
    session to borrow, and running it unauthenticated is exactly the defect
    this closes.
    """
    global _SERVICE_SESSION
    from agent_utilities.api.session import SessionExpiredError

    with _SERVICE_SESSION_LOCK:
        if _SERVICE_SESSION is not None:
            try:
                _SERVICE_SESSION.ensure_authority_current(
                    minimum_ttl_seconds=_SERVICE_AUTHORITY_RENEWAL_MARGIN_SECONDS
                )
                return _SERVICE_SESSION
            except SessionExpiredError:
                logger.info(
                    "cached gateway service authority is within %ss of "
                    "expiry (or already expired); minting a replacement "
                    "before reuse",
                    _SERVICE_AUTHORITY_RENEWAL_MARGIN_SECONDS,
                )
        from graph_os.gateway.daemon import mint_process_identity

        _SERVICE_SESSION = mint_process_identity()
        return _SERVICE_SESSION


def _run_as_service_actor(fn: Callable[[], _T]) -> _T:
    """Run synchronous ``fn`` with the gateway's service actor bound.

    Called *inside* the worker thread (see ``_fetch_one``/``health_check``
    below): ``run_in_executor`` does not copy the submitting coroutine's
    contextvars into the worker thread, so ``use_actor``/``use_session`` must
    be entered here, not in the event-loop coroutine, or the bound context
    never crosses the executor boundary and the widget call still finds no
    actor (the exact defect this fixes).
    """
    from agent_utilities.api.session import use_session
    from agent_utilities.security.brain_context import use_actor

    session = _service_authority()
    with use_actor(session.actor), use_session(session):
        return fn()


class Aggregator:
    """Fetches data from all configured service widgets in parallel.

    Uses a thread pool since most agent-package API clients are synchronous
    (requests-based). Each widget.fetch_data() call runs in its own thread.
    """

    def __init__(
        self,
        registry: Registry | None = None,
        config_manager: ConfigManager | None = None,
        max_workers: int = 10,
    ):
        self.registry = registry or get_registry()
        self.config_manager = config_manager or ConfigManager()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._cache: dict[str, tuple[WidgetData, float]] = {}
        self._cache_ttl: float = 10.0  # seconds

    async def fetch_all(self) -> dict[str, WidgetData]:
        """Fetch data from all configured services concurrently.

        Returns:
            Dict mapping service_id to WidgetData.
        """
        layout = self.config_manager.load()
        services = [
            svc for group in layout.groups for svc in group.services if svc.visible
        ]

        results = {
            svc.id: cached
            for svc in services
            if (cached := self._get_cached(svc.id)) is not None
        }
        pending = [svc for svc in services if svc.id not in results]
        fetched = await asyncio.gather(
            *(self._fetch_one(svc) for svc in pending), return_exceptions=True
        )
        for svc, result in zip(pending, fetched, strict=False):
            results[svc.id] = self._record_result(svc.id, result)
        return results

    def _record_result(
        self, service_id: str, result: WidgetData | BaseException
    ) -> WidgetData:
        if isinstance(result, BaseException):
            payload = public_error_payload(result, logger=logger)
            return WidgetData(
                status="error",
                error=payload["message"],
                raw={
                    "code": payload["code"],
                    "correlation_id": payload["correlation_id"],
                },
            )
        self._set_cached(service_id, result)
        return result

    async def fetch_one(self, service_id: str) -> WidgetData:
        """Fetch data for a single service by ID."""
        services = self.config_manager.get_all_services()
        svc = next((s for s in services if s.id == service_id), None)
        if not svc:
            return WidgetData(status="error", error="service not found")
        return await self._fetch_one(svc)

    async def _fetch_one(self, config: ServiceConfig) -> WidgetData:
        """Fetch data for a single service using the thread pool."""
        widget = self.registry.get_widget(config.widget_type)
        if not widget:
            return WidgetData(
                status="error",
                error="widget type is unavailable",
            )

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            _run_as_service_actor,
            lambda: widget._safe_fetch(config),
        )

    async def stream(
        self, interval: float = 30.0
    ) -> AsyncIterator[dict[str, WidgetData]]:
        """Continuously stream dashboard data at the given interval.

        Usage (TUI/GUI)::

            async for data in aggregator.stream(interval=10):
                update_display(data)
        """
        while True:
            data = await self.fetch_all()
            yield data
            await asyncio.sleep(interval)

    async def health_check(self) -> dict[str, bool]:
        """Quick health check across all configured services."""
        layout = self.config_manager.load()
        services = [svc for group in layout.groups for svc in group.services]

        results: dict[str, bool] = {}
        loop = asyncio.get_event_loop()

        tasks = []
        scheduled: list[ServiceConfig] = []
        for svc in services:
            widget = self.registry.get_widget(svc.widget_type)
            if widget:
                tasks.append(
                    loop.run_in_executor(
                        self._executor,
                        _run_as_service_actor,
                        lambda widget=widget, svc=svc: widget.check_health(svc),
                    )
                )
                scheduled.append(svc)
            else:
                results[svc.id] = False

        health_results = await asyncio.gather(*tasks, return_exceptions=True)
        for svc, result in zip(scheduled, health_results, strict=False):
            results[svc.id] = not isinstance(result, Exception) and bool(result)

        return results

    def get_layout(self) -> DashboardLayout:
        """Get the current dashboard layout."""
        return self.config_manager.load()

    def save_layout(self, layout: DashboardLayout) -> None:
        """Save dashboard layout to YAML."""
        self.config_manager.save(layout)

    def _get_cached(self, service_id: str) -> WidgetData | None:
        """Get cached data if still fresh."""
        if service_id in self._cache:
            data, ts = self._cache[service_id]
            if time.time() - ts < self._cache_ttl:
                return data
        return None

    def _set_cached(self, service_id: str, data: WidgetData) -> None:
        """Cache widget data with timestamp."""
        self._cache[service_id] = (data, time.time())
