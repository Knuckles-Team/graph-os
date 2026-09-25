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


def start_messaging_intake(
    supervisor: CoServiceStarter,
    engine: Any,
    session: Any,
    platforms: tuple[str, ...],
) -> None:
    """Admit the configured channels to the verified GraphOS host.

    This owns the process lifecycle decision. The current AU intake executor
    still owns the lease and router until its engine ports move here.
    """
    if session is None:
        raise PermissionError("verified messaging session is required")
    if not platforms:
        raise ValueError("messaging intake requires configured platforms")

    from agent_utilities.messaging.daemon import run_forever

    def run_messaging(stop_event: threading.Event) -> None:
        run_forever(
            engine,
            list(platforms),
            stop_event,
            session=session,
            intake_intent=True,
        )

    supervisor.start_service("messaging", run_messaging, session)
