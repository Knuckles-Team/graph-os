"""The native GraphOS host owns messaging intake admission."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from graph_os.messaging.intake import start_messaging_intake


def test_messaging_intake_requires_verified_session_and_channels() -> None:
    class Supervisor:
        def start_service(self, *_args: Any) -> None:
            raise AssertionError("intake must fail before starting a service")

    with pytest.raises(PermissionError, match="verified messaging session"):
        start_messaging_intake(Supervisor(), object(), None, ("telegram",))
    with pytest.raises(ValueError, match="configured platforms"):
        start_messaging_intake(Supervisor(), object(), object(), ())


def test_messaging_intake_passes_explicit_intent_and_verified_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    session = object()
    stop_event = threading.Event()
    started: list[tuple[str, Callable[[threading.Event], None], object]] = []
    called: list[tuple[Any, list[str], threading.Event, Any, bool]] = []

    class Supervisor:
        def start_service(
            self,
            name: str,
            run: Callable[[threading.Event], None],
            verified_session: Any,
        ) -> None:
            started.append((name, run, verified_session))

    def run_forever(
        served_engine: Any,
        platforms: list[str],
        stop: threading.Event,
        *,
        session: Any,
        intake_intent: bool,
    ) -> None:
        called.append((served_engine, platforms, stop, session, intake_intent))

    monkeypatch.setattr("agent_utilities.messaging.daemon.run_forever", run_forever)

    start_messaging_intake(Supervisor(), engine, session, ("telegram", "mattermost"))
    assert len(started) == 1
    assert started[0][0] == "messaging"
    assert started[0][2] is session
    started[0][1](stop_event)
    assert called == [(engine, ["telegram", "mattermost"], stop_event, session, True)]
