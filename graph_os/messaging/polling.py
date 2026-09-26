"""GraphOS-owned inbound messaging poll loop and channel orchestration.

The served GraphOS host enters here only after the engine-native channel leases
have admitted intake. Agent command handling and the universal agent stay in AU.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)
_MESSAGING_LOG_HANDLER_MARK = "_graphos_messaging_visibility_handler"


def _ensure_messaging_log_visibility() -> None:
    """Keep channel and router lifecycle logs visible on stderr under stdio."""
    from agent_utilities.core.config import setting

    level_name = str(setting("MESSAGING_LOG_LEVEL", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    for namespace in ("graph_os.messaging", "agent_utilities.messaging"):
        pkg_logger = logging.getLogger(namespace)
        pkg_logger.setLevel(level)
        pkg_logger.disabled = False
        if not any(
            getattr(h, _MESSAGING_LOG_HANDLER_MARK, False) for h in pkg_logger.handlers
        ):
            handler = logging.StreamHandler(stream=sys.stderr)
            handler.setLevel(level)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            setattr(handler, _MESSAGING_LOG_HANDLER_MARK, True)
            pkg_logger.addHandler(handler)
        pkg_logger.propagate = False


async def _serve(engine: Any, platforms: list[str], router_box: dict[str, Any]) -> None:
    """Connect configured backends and run the InboundRouter (blocks on listeners).

    ``router_box`` is a caller-owned handoff cell: it is populated with the
    live ``InboundRouter`` the moment it is constructed, BEFORE any backend
    connects, so a concurrent per-platform lease-loss watcher
    (:func:`run_poll_loop`) can reach in and cancel exactly one platform's
    listener task later — without this coroutine needing to know anything
    about leases itself.
    """
    from agent_utilities.messaging.commands import command_specs
    from agent_utilities.messaging.router import create_planner_handler

    from graph_os.messaging.router import InboundRouter
    from graph_os.messaging.service import MessagingService

    svc = MessagingService.instance(engine)
    router = InboundRouter()
    router_box["router"] = router
    for pid in platforms:
        backend = await svc.get_backend(pid)
        if backend is None:
            continue
        svc.register_connected(backend)
        # Publish OUR universal command set (CONCEPT:AU-ECO.messaging.single-inbound-command-dispatcher) where the platform supports
        # a runtime menu; a no-op elsewhere.
        try:
            await backend.register_commands(command_specs("messaging"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("messaging: register_commands(%s) failed: %s", pid, exc)
        router.register_backend(backend)
    router.set_default_handler(await create_planner_handler(engine))
    logger.info(
        "[CONCEPT:AU-ECO.messaging.inbound-messaging-router-runs] messaging serving backends %s",
        platforms,
    )
    await router.start()  # blocks on the per-backend listener tasks


def run_poll_loop(
    engine: Any,
    platforms: list[str],
    stop_event: threading.Event,
    platform_stop_events: dict[str, threading.Event] | None = None,
) -> None:
    """Low-level serving body, called only after GraphOS lease fencing.

    Owns its OWN event loop (asyncio state is not shareable across threads), so this
    body deliberately has no ownership checks of its own: keeping it separate
    makes it impossible for ownership state to diverge from the shared native
    lease helper. It must not be called by an entrypoint directly.

    ``platform_stop_events`` (one ``threading.Event`` per platform, set by the
    lease renewal loop in :mod:`agent_utilities.messaging.intake_lease` the
    moment that platform's lease is lost) lets exactly ONE platform's listener
    be torn down without touching the others: a task on the owning event loop
    cancels only that platform's supervise task inside the single shared
    ``InboundRouter`` — the healthy platforms' listener tasks, and the shared
    inbox reaper, are never cancelled and never even observe the event. This
    is the fix for the platform that loses its lease otherwise taking every
    other platform down with it (they previously all lived under the ONE
    ``_serve`` task this function cancels wholesale on ``stop_event``).
    """
    # Guarantee this co-service's lifecycle/error logs reach stderr (→ kubectl logs)
    # regardless of the graph-os root logger being pinned to WARNING at build time. Safe
    # + idempotent across supervised co-service restarts.
    _ensure_messaging_log_visibility()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    router_box: dict[str, Any] = {}

    def _drop_platform(platform: str) -> bool:
        """Cancel ONLY ``platform``'s listener task inside the live router."""
        router = router_box.get("router")
        if router is None:
            return False
        target_name = f"messaging-router-{platform}"
        for task in list(getattr(router, "_tasks", ())):
            if task.get_name() == target_name:
                if not task.done():
                    task.cancel()
                    logger.error(
                        "[CONCEPT:AU-ECO.messaging.inbound-messaging-router-runs] "
                        "messaging dropped platform=%s from inbound routing "
                        "(lease lost); other platforms continue serving",
                        platform,
                    )
                return True
        return True  # no listener was started for this platform

    async def _watch_intake() -> None:
        """Observe lease loss on the owning loop without cross-thread wakeups."""
        dropped: set[str] = set()
        while not stop_event.is_set():
            for platform, platform_stop in (platform_stop_events or {}).items():
                if platform_stop.is_set() and platform not in dropped:
                    if _drop_platform(platform):
                        dropped.add(platform)
            await asyncio.sleep(0.25)

    tasks = [loop.create_task(_serve(engine, platforms, router_box))]
    # Optional HTTP alert-intake (CONCEPT:AU-ECO.messaging.alert-intake): route external
    # webhooks (uptime-kuma, Alertmanager, …) THROUGH the messaging stack so alerts inherit
    # the one unified Telegram/Mattermost/… delivery instead of each tool wiring its own
    # notifier. Opt-in via MESSAGING_ALERT_INTAKE_PORT; runs as an INDEPENDENT task so a
    # failure here never affects the inbound listeners.
    from agent_utilities.core.config import setting

    _intake_port = setting("MESSAGING_ALERT_INTAKE_PORT", "")
    if _intake_port:
        from graph_os.messaging.alert_intake import serve_alert_intake

        tasks.append(loop.create_task(serve_alert_intake(engine, int(_intake_port))))
    logger.info(
        "[CONCEPT:AU-ECO.messaging.inbound-messaging-router-runs] messaging serving started."
    )
    try:
        loop.run_until_complete(_watch_intake())
    finally:
        for _t in tasks:
            _t.cancel()
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.close()
