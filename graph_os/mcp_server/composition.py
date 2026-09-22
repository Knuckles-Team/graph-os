"""Graph-os composition adapters for gateway and supervised co-services."""

from __future__ import annotations

import contextvars
import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from graph_os.gateway.ports import configure_gateway_application
from graph_os.mcp_server import runtime
from graph_os.mcp_server.routes import mount_rest_routes
from graph_os.webui_host import run_web_ui

logger = logging.getLogger(__name__)

_MAX_RESTARTS = 5
_RESTART_WINDOW_SECONDS = 300.0
_MAX_BACKOFF_SECONDS = 30.0


@dataclasses.dataclass(frozen=True)
class CompositionPlan:
    """GraphOS-owned decision about optional co-services."""

    messaging_platforms: tuple[str, ...] = ()
    web_ui_enabled: bool = False
    messaging_intake_enabled: bool = False

    @property
    def messaging_configured(self) -> bool:
        return bool(self.messaging_platforms)

    @property
    def messaging_intake_configured(self) -> bool:
        return self.messaging_configured and self.messaging_intake_enabled


def detect_composition(
    engine: Any = None, *, messaging_intake_enabled: bool | None = None
) -> CompositionPlan:
    """Read optional co-service intent without starting background work."""
    from agent_utilities.core.config import config
    from agent_utilities.messaging.daemon import configured_platforms

    return CompositionPlan(
        messaging_platforms=tuple(configured_platforms(engine)),
        web_ui_enabled=bool(getattr(config, "enable_web_ui", False)),
        messaging_intake_enabled=bool(messaging_intake_enabled),
    )


class CoServiceSupervisor:
    """Own optional GraphOS co-services under the caller's verified context."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, tuple[threading.Event, threading.Thread]] = {}

    def start_service(
        self,
        name: str,
        run: Callable[[threading.Event], None],
        session: Any,
    ) -> None:
        """Start one bounded-restart service in a copy of the ambient context."""
        if session is None:
            raise PermissionError("verified co-service session is required")
        context = contextvars.copy_context()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=context.run,
            args=(self._run_supervised, name, run, stop_event),
            name=f"CoService-{name}",
            daemon=True,
        )
        with self._lock:
            if name in self._services:
                raise RuntimeError(f"co-service {name!r} is already running")
            self._services[name] = (stop_event, thread)
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._services.pop(name, None)
            raise
        logger.info("co-service %s started", name)

    def _run_supervised(
        self,
        name: str,
        run: Callable[[threading.Event], None],
        stop_event: threading.Event,
    ) -> None:
        restarts: list[float] = []
        while not stop_event.is_set():
            self._run_once(name, run, stop_event)
            if stop_event.is_set():
                return
            now = time.monotonic()
            restarts = [
                then for then in restarts if now - then < _RESTART_WINDOW_SECONDS
            ]
            restarts.append(now)
            if len(restarts) > _MAX_RESTARTS:
                logger.error("co-service %s exceeded its restart bound", name)
                return
            stop_event.wait(min(2.0 ** len(restarts), _MAX_BACKOFF_SECONDS))

    @staticmethod
    def _run_once(
        name: str,
        run: Callable[[threading.Event], None],
        stop_event: threading.Event,
    ) -> None:
        try:
            run(stop_event)
        except Exception:
            logger.exception("co-service %s crashed", name)
        else:
            if not stop_event.is_set():
                logger.error("co-service %s exited without a stop request", name)

    def stop_all(self, timeout: float = 10.0) -> bool:
        """Signal every service and retain any handle that remains live."""
        with self._lock:
            services = list(self._services.items())
        for _name, (stop_event, _thread) in services:
            stop_event.set()
        for name, (_stop_event, thread) in services:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.error("co-service %s did not stop within %.0fs", name, timeout)
                continue
            with self._lock:
                self._services.pop(name, None)
        return not self.running()

    def running(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                name
                for name, (_event, thread) in self._services.items()
                if thread.is_alive()
            )


class NativeGatewayApplication:
    """Concrete application behavior behind the gateway transport port."""

    async def execute_tool(self, tool: str, /, **kwargs: Any) -> Any:
        return await runtime._execute_tool(tool, **kwargs)

    def engine(self) -> Any:
        return runtime._get_engine()

    def ensure_tools_registered(self) -> None:
        runtime.ensure_tools_registered()

    def mount_rest_routes(self, app: Any, *, prefix: str) -> None:
        mount_rest_routes(app, prefix=prefix)

    def remote_oauth_grant_bindings(self, actor: Any) -> Sequence[Any]:
        from graph_os.fleet.multiplexer import (
            current_remote_oauth_grant_bindings,
        )

        return current_remote_oauth_grant_bindings(actor)


def install_gateway_application() -> NativeGatewayApplication:
    """Install the native application adapter at the process composition root."""

    application = NativeGatewayApplication()
    configure_gateway_application(application)
    return application


def start_composed_services(
    session: Any,
    engine: Any,
    *,
    messaging_intake_enabled: bool | None = None,
) -> CoServiceSupervisor:
    """Start AU agent-plane messaging and the graph-os-owned WebUI host."""

    plan = detect_composition(engine, messaging_intake_enabled=messaging_intake_enabled)
    supervisor = CoServiceSupervisor()
    if plan.messaging_intake_configured:
        from agent_utilities.messaging.daemon import run_forever

        platforms = list(plan.messaging_platforms)

        def run_messaging(stop_event: threading.Event) -> None:
            run_forever(
                engine,
                platforms,
                stop_event,
                session=session,
                intake_intent=True,
            )

        supervisor.start_service("messaging", run_messaging, session)
    elif plan.messaging_configured:
        logger.info("messaging credentials are present but inbound intake is disabled")
    if plan.web_ui_enabled:
        supervisor.start_service("agent-webui", run_web_ui, session)
    return supervisor
