"""The native GraphOS host owns messaging intake admission."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from graph_os.messaging import intake
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
    called: list[tuple[Any, tuple[str, ...], threading.Event, Any, bool]] = []

    class Supervisor:
        def start_service(
            self,
            name: str,
            run: Callable[[threading.Event], None],
            verified_session: Any,
        ) -> None:
            started.append((name, run, verified_session))

    def run_owned_intake(
        served_engine: Any,
        platforms: tuple[str, ...],
        stop: threading.Event,
        session: Any,
        *,
        intake_intent: bool,
    ) -> None:
        called.append((served_engine, platforms, stop, session, intake_intent))

    monkeypatch.setattr(intake, "run_owned_intake", run_owned_intake)

    start_messaging_intake(Supervisor(), engine, session, ("telegram", "mattermost"))
    assert len(started) == 1
    assert started[0][0] == "messaging"
    assert started[0][2] is session
    started[0][1](stop_event)
    assert called == [(engine, ("telegram", "mattermost"), stop_event, session, True)]


def test_graphos_intake_passes_only_leased_channels_to_poller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    session = object()
    stop_event = threading.Event()
    seen: list[Any] = []

    def run_leased(
        served_engine: Any,
        channels: list[str],
        verified_session: Any,
        stop: threading.Event,
        serve: Callable[[list[str], threading.Event, dict[str, threading.Event]], None],
    ) -> None:
        seen.append((served_engine, channels, verified_session, stop))
        serve(["telegram"], stop, {"telegram": threading.Event()})

    def poll(
        served_engine: Any,
        channels: list[str],
        stop: threading.Event,
        channel_stops: dict[str, threading.Event],
    ) -> None:
        seen.append((served_engine, channels, stop, channel_stops))

    monkeypatch.setattr("graph_os.messaging.lease.run_owned_intake", run_leased)
    monkeypatch.setattr("graph_os.messaging.polling.run_poll_loop", poll)
    intake.run_owned_intake(
        engine, ("telegram", "mattermost"), stop_event, session, intake_intent=True
    )

    assert seen[0] == (engine, ["telegram", "mattermost"], session, stop_event)
    assert seen[1][0:3] == (engine, ["telegram"], stop_event)
    assert set(seen[1][3]) == {"telegram"}


def test_direct_intake_without_explicit_intent_refuses_to_poll() -> None:
    with pytest.raises(PermissionError, match="explicit intake intent"):
        intake.run_owned_intake(object(), ("telegram",), threading.Event(), object())


def test_configured_platforms_uses_reach_service_without_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()

    class Service:
        def configured_platforms(self) -> list[str]:
            return ["telegram"]

    from graph_os.messaging.service import MessagingService

    monkeypatch.setattr(MessagingService, "instance", lambda arg: Service())
    assert intake.configured_platforms(engine) == ("telegram",)
