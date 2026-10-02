"""GraphOS admission and lifecycle handoff for messaging intake."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol


class CoServiceStarter(Protocol):
    """The verified host's one co-service supervisor entrypoint."""

    def start_service(
        self,
        name: str,
        run: Callable[[threading.Event], None],
        session: Any,
    ) -> None: ...


def configured_platforms(engine: Any = None) -> tuple[str, ...]:
    """Discover installed, credentialed channels without opening a poller."""
    from graph_os.messaging.service import MessagingService

    return tuple(MessagingService.instance(engine).configured_platforms())


def run_owned_intake(
    engine: Any,
    platforms: tuple[str, ...],
    stop_event: threading.Event,
    session: Any,
    *,
    intake_intent: bool = False,
) -> None:
    """Fence GraphOS polling with the durable per-channel lease authority."""
    if session is None or not intake_intent:
        raise PermissionError(
            "explicit intake intent and verified messaging session are required"
        )
    if not platforms:
        raise ValueError("messaging intake requires configured platforms")

    from graph_os.messaging.lease import run_owned_intake as run_leased
    from graph_os.messaging.polling import run_poll_loop

    run_leased(
        engine,
        list(platforms),
        session,
        stop_event,
        lambda owned_platforms, owned_stop_event, platform_stop_events: run_poll_loop(
            engine, owned_platforms, owned_stop_event, platform_stop_events
        ),
    )


def start_messaging_intake(
    supervisor: CoServiceStarter,
    engine: Any,
    session: Any,
    platforms: tuple[str, ...],
) -> None:
    """Admit the configured channels to the verified GraphOS host.

    GraphOS owns the process lifecycle, engine-native lease boundary, and poll loop.
    """
    if session is None:
        raise PermissionError("verified messaging session is required")
    if not platforms:
        raise ValueError("messaging intake requires configured platforms")

    def run_messaging(stop_event: threading.Event) -> None:
        run_owned_intake(engine, platforms, stop_event, session, intake_intent=True)

    supervisor.start_service("messaging", run_messaging, session)
