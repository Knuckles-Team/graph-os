"""GraphOS inbound channel router and listener supervision.

Agent Utilities supplies the planner handler; GraphOS owns channel receive,
backend supervision, dispatch, and durable inbox retry orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agent_utilities.messaging.models import EventType, InboundEvent

if TYPE_CHECKING:
    from agent_utilities.messaging.base import MessagingBackend

logger = logging.getLogger(__name__)
EventHandler = Callable[[InboundEvent, "MessagingBackend"], Awaitable[None]]


class InboundRouter:
    """Routes inbound messaging events to the planner graph agent.

    CONCEPT:AU-ECO.messaging.native-backend-abstraction — Native Messaging Backend Abstraction

    The router listens on all connected backends simultaneously and
    dispatches events to registered handlers. The default handler runs
    each chat turn through the universal graph agent (CONCEPT:AU-ECO.messaging.universal-graph-agent),
    which:

    1. Recalls prior turns of this channel from the core memory (session-scoped mementos)
    2. Dynamically resolves which agents / skills / tools should handle it
    3. Sends the response back through the originating backend

    Usage::

        from graph_os.messaging.registry import MessagingRegistry
        from graph_os.messaging.router import InboundRouter

        registry = MessagingRegistry()
        discord = registry.create_backend("discord")
        await discord.connect()

        router = InboundRouter()
        router.register_backend(discord)

        # Start listening (blocks until cancelled)
        await router.start()

    Attributes:
        _backends: Connected messaging backends to listen on.
        _handlers: Registered event handlers by event type.
        _default_handler: Handler for unmatched events.
        _running: Whether the router is currently active.
        _tasks: Active listener tasks.
    """

    def __init__(self) -> None:
        self._backends: list[MessagingBackend] = []
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._default_handler: EventHandler | None = None
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    def register_backend(self, backend: MessagingBackend) -> None:
        """Register a connected messaging backend for event listening.

        Args:
            backend: A connected ``MessagingBackend`` instance.
        """
        self._backends.append(backend)
        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Registered backend '%s' for inbound routing.",
            backend.id,
        )

    def on_event(self, event_type: EventType) -> Callable[[EventHandler], EventHandler]:
        """Decorator to register a handler for a specific event type.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Usage::

            @router.on_event(EventType.MESSAGE)
            async def handle_message(event, backend):
                await backend.reply_to(event.channel_id, event.target_message_id, "Got it!")

        Args:
            event_type: The event type to handle.

        Returns:
            Decorator function.
        """

        def decorator(func: EventHandler) -> EventHandler:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(func)
            return func

        return decorator

    def set_default_handler(self, handler: EventHandler) -> None:
        """Set the default handler for events without a specific handler.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        The default handler is typically the planner graph agent
        dispatcher, which routes messages through the KG-aware
        orchestration pipeline.

        Args:
            handler: Async function taking (InboundEvent, MessagingBackend).
        """
        self._default_handler = handler

    async def start(self) -> None:
        """Start listening on all registered backends.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Creates an async task for each backend's ``listen()`` method
        and dispatches events to registered handlers.
        """
        self._running = True
        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Starting inbound router with %d backends.",
            len(self._backends),
        )

        for backend in self._backends:
            if not backend.is_connected:
                logger.warning(
                    "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Backend '%s' is not connected, skipping.",
                    backend.id,
                )
                continue
            task = asyncio.create_task(
                self._supervise_backend(backend),
                name=f"messaging-router-{backend.id}",
            )
            self._tasks.append(task)

        # CONCEPT:AU-ECO.messaging.durable-inbound-pending — durable-inbox reaper: re-answers inbound turns that were recorded
        # pending but never got a reply (engine down / crashed mid-flight), so nothing is lost.
        if self._backends:
            self._tasks.append(
                asyncio.create_task(
                    self._inbox_reaper_loop(), name="messaging-inbox-reaper"
                )
            )

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _backend_for(self, platform: str) -> MessagingBackend | None:
        """The connected backend for a platform id (CONCEPT:AU-ECO.messaging.durable-inbound-pending), for reaper retries."""
        for b in self._backends:
            if platform and (
                str(getattr(b, "id", "")) == platform
                or str(getattr(b, "platform", "")) == platform
            ):
                return b
        return self._backends[0] if self._backends else None

    async def _inbox_reaper_loop(self) -> None:
        """Periodically re-attempt durably-recorded but still-unanswered inbound messages
        (CONCEPT:AU-ECO.messaging.durable-inbound-pending). Uses this router's own backends + the universal reply path, so a
        turn that failed while the engine was down is answered once the system recovers."""
        from agent_utilities.core.config import setting

        from graph_os.messaging.inbox import retry_unanswered
        from graph_os.messaging.service import MessagingService

        interval = float(setting("MESSAGING_INBOX_RETRY_S", "120"))
        while self._running:
            await asyncio.sleep(interval)
            try:
                engine = MessagingService.instance()._resolve_engine()

                async def _reply_send(m: dict[str, Any], _eng: Any = engine) -> bool:
                    backend = self._backend_for(str(m.get("platform", "")))
                    if backend is None:
                        return False
                    # The agent runtime's public API package does not publish a
                    # reply surface yet (tracked under GRAPHOS-HOST-R007); reach
                    # its existing universal-reply path directly until it does.
                    from agent_utilities.messaging.router import (
                        _graph_agent_reply as graph_agent_reply,
                    )

                    reply = await graph_agent_reply(
                        _eng, m.get("text", ""), session=m.get("session", "")
                    )
                    if not reply:
                        return False
                    result = await backend.send_message(m.get("channel_id", ""), reply)
                    return bool(result.success)

                await retry_unanswered(engine, _reply_send)
            except Exception as e:  # the reaper must survive any single pass
                logger.debug(
                    "[CONCEPT:AU-ECO.messaging.durable-inbound-pending] inbox reaper pass failed: %s",
                    e,
                )

    async def stop(self) -> None:
        """Stop all listener tasks gracefully.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction
        """
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Inbound router stopped."
        )

    async def _supervise_backend(self, backend: MessagingBackend) -> None:
        """Keep a backend's listener ALIVE across recoverable failures (self-healing).

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Runs :meth:`_listen_loop` (one ``listen()`` attempt) inside a supervision loop.
        On an unexpected exception — anything that is NOT ``asyncio.CancelledError``
        (clean shutdown) and NOT ``NotImplementedError`` (the backend genuinely cannot
        listen) — it logs the failure clearly and RESTARTS ``listen()`` after an
        exponential backoff (base → ×2 → … capped). This is the durability fix: a
        transient error such as a Telegram ``getUpdates`` 409 restart race (a new pod's
        poller colliding with the old pod's still-expiring long-poll) used to be caught,
        logged, and left to END the listener task permanently — so Telegram never
        recovered until a full process restart, which raced again. Now it recovers
        automatically within seconds.

        The backoff is BOUNDED (never busy-loops) and is RESET to the base after a
        sustained healthy run, so a brief blip does not leave the backend slow to retry
        while a persistently-failing backend still backs off toward the cap. Tunables
        (all read live): ``MESSAGING_LISTEN_BACKOFF_BASE_S`` (default 1s),
        ``MESSAGING_LISTEN_BACKOFF_MAX_S`` (default 60s),
        ``MESSAGING_LISTEN_HEALTHY_RESET_S`` (default 60s — a run that stayed up at least
        this long is deemed healthy and resets the backoff).

        Args:
            backend: The messaging backend to supervise.
        """
        from agent_utilities.core.config import setting

        base = max(0.0, float(setting("MESSAGING_LISTEN_BACKOFF_BASE_S", "1")))
        cap = max(base, float(setting("MESSAGING_LISTEN_BACKOFF_MAX_S", "60")))
        healthy_reset = float(setting("MESSAGING_LISTEN_HEALTHY_RESET_S", "60"))
        # A zero base would busy-loop on a hard-failing backend; floor the delay.
        delay = base or 1.0
        while self._running:
            started = time.monotonic()
            restart_reason = await self._run_one_listen_attempt(backend, delay)
            if restart_reason is None:
                return
            ran_for = time.monotonic() - started
            if not self._running:
                return
            self._record_listener_restart(backend.id, delay, ran_for)
            # Enforce the backoff so a hard-failing backend never busy-loops; a cancel
            # during the wait is a clean shutdown and propagates out of the coroutine.
            await asyncio.sleep(delay)
            # Reset the backoff after a sustained healthy run; otherwise grow it (capped).
            delay = (base or 1.0) if ran_for >= healthy_reset else min(delay * 2.0, cap)

    async def _run_one_listen_attempt(
        self, backend: MessagingBackend, delay: float
    ) -> str | None:
        """One supervised ``_listen_loop`` attempt for ``_supervise_backend``.

        Extracted verbatim from ``_supervise_backend`` (pure extract-method, no
        behaviour change). Returns a ``restart_reason`` (``"error"`` /
        ``"stream_closed"``) when the caller should restart after a backoff,
        or ``None`` when the caller should return immediately
        (``NotImplementedError``, or the router stopped mid-run). Re-raises
        ``asyncio.CancelledError`` for a clean shutdown, exactly as before.
        """
        try:
            await self._listen_loop(backend)
        except asyncio.CancelledError:
            # Clean shutdown (router.stop cancelled us) — never restart; propagate.
            logger.debug(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Listener cancelled for '%s'.",
                backend.id,
            )
            raise
        except NotImplementedError:
            # The backend cannot listen (outbound-only) — giving up is correct.
            logger.warning(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Backend '%s' does not support "
                "inbound listening — not restarting.",
                backend.id,
            )
            return None
        except Exception as e:  # supervise: log + backed-off restart, never die
            logger.error(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Listener for '%s' failed: %s "
                "— restarting in %.1fs (self-healing supervisor).",
                backend.id,
                e,
                delay,
                exc_info=True,
            )
            return "error"
        else:
            # ``listen()`` returned without error. If we are shutting down, exit;
            # otherwise the stream closed unexpectedly (a long-poll/websocket backend
            # should not) — treat it as recoverable and restart after the backoff.
            if not self._running:
                return None
            logger.warning(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Listener for '%s' ended "
                "unexpectedly (stream closed without error) — restarting in %.1fs.",
                backend.id,
                delay,
            )
            return "stream_closed"

    def _record_listener_restart(
        self, backend_id: str, delay: float, ran_for: float
    ) -> None:
        """CONCEPT:AU-AHE.harness.runtime-reliability-loop — record the self-heal
        so the runtime-reliability loop SEES it, from ``_supervise_backend``: a
        listener that keeps dying+restarting (e.g. the Telegram 409 race) is
        auto-healed there, but repeated restarts are a SOURCE_RUNTIME signal
        the flywheel should note (the reconciler records them as a resolved
        heal). Fire-and-forget; never perturbs the supervisor.

        Extracted verbatim from ``_supervise_backend`` (pure extract-method, no
        behaviour change).
        """
        try:
            from agent_utilities.observability.runtime_signals import (
                KIND_LISTENER_RESTART,
                record_runtime_signal,
            )

            record_runtime_signal(
                KIND_LISTENER_RESTART,
                backend_id,
                {"delay_s": round(delay, 2), "ran_for_s": round(ran_for, 2)},
            )
        except Exception:  # emission must never affect supervision
            pass

    async def _listen_loop(self, backend: MessagingBackend) -> None:
        """One ``listen()`` attempt for a single backend — consumes its event stream.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Consumes the backend's ``listen()`` async iterator and dispatches each event to
        the appropriate handler. Exceptions are deliberately NOT swallowed here (they
        were before, which permanently killed the listener on the first error): this
        method is driven by :meth:`_supervise_backend`, so ``CancelledError`` propagates
        for a clean shutdown, ``NotImplementedError`` signals "backend can't listen", and
        every other exception propagates to the supervisor for a backed-off restart.

        Args:
            backend: The messaging backend to listen on.
        """
        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Listening for events on '%s'...",
            backend.id,
        )
        async for event in backend.listen():
            if not self._running:
                break
            await self._dispatch(event, backend)

    async def _dispatch(self, event: InboundEvent, backend: MessagingBackend) -> None:
        """Dispatch an event to registered handlers.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Priority:
        1. Specific event-type handlers (registered via ``on_event``)
        2. Default handler (typically the planner graph agent)
        3. Log and discard if no handler matches

        Args:
            event: The inbound event to dispatch.
            backend: The backend that received the event.
        """
        handlers = self._handlers.get(event.event_type, [])

        if handlers:
            for handler in handlers:
                try:
                    await handler(event, backend)
                except Exception as e:
                    logger.error(
                        "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Handler error for %s event: %s",
                        event.event_type,
                        e,
                        exc_info=True,
                    )
        elif self._default_handler:
            try:
                await self._default_handler(event, backend)
            except Exception as e:
                logger.error(
                    "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Default handler error: %s",
                    e,
                    exc_info=True,
                )
        else:
            logger.debug(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] No handler for %s event from '%s'.",
                event.event_type,
                backend.id,
            )
