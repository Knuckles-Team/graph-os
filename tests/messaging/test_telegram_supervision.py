"""Telegram conflict behavior across the GraphOS adapter and AU router."""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_utilities.messaging.models import EventType

from graph_os.messaging.router import InboundRouter


def _msg_event() -> Any:
    return types.SimpleNamespace(event_type=EventType.MESSAGE)


# ── FIX 1 part B: Telegram getUpdates 409 (Conflict) handling ────────────────


@pytest.fixture()
def fake_telegram(monkeypatch: pytest.MonkeyPatch) -> type[Exception]:
    """Inject a minimal ``telegram.error`` module so the backend's lazy
    ``from telegram.error import Conflict`` resolves without python-telegram-bot
    installed. Returns the fake ``Conflict`` class."""
    tg = types.ModuleType("telegram")
    err = types.ModuleType("telegram.error")

    class Conflict(Exception):
        pass

    err.Conflict = Conflict  # type: ignore[attr-defined]
    tg.error = err  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telegram", tg)
    monkeypatch.setitem(sys.modules, "telegram.error", err)
    return Conflict


async def test_telegram_conflict_propagates_and_stops_updater_cleanly(
    fake_telegram: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX 1 part B: a 409 ``Conflict`` on ``start_polling`` must stop the updater cleanly
    (no leaked half-open poller) and PROPAGATE to the supervisor — not be swallowed."""
    from agent_utilities.messaging.models import MessagingConfig

    from graph_os.messaging.backends.telegram import TelegramBackend

    conflict_cls = fake_telegram
    monkeypatch.setenv("MESSAGING_WEBHOOK_BASE_URL", "")  # force the polling path

    stop_calls: list[bool] = []

    class _Updater:
        def __init__(self) -> None:
            self.running = True

        async def start_polling(self) -> None:
            raise conflict_cls("terminated by other getUpdates request")

        async def stop(self) -> None:
            stop_calls.append(True)
            self.running = False

    class _App:
        updater = _Updater()

    backend = TelegramBackend(MessagingConfig(token="123456789:SECRETPART"))
    backend._connected = True
    backend._app = _App()  # type: ignore[assignment]

    with pytest.raises(conflict_cls):
        await backend._start_intake()

    assert backend._polling is False  # not left half-open
    assert stop_calls == [True]  # updater.stop() ran on the way out (clean stop)


async def test_telegram_conflict_reaches_supervisor_and_retries(
    fake_telegram: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX 1 end-to-end (mocked): a Telegram ``listen()`` that raises ``Conflict`` once is
    restarted by the supervisor and then serves — proving the 409 no longer permanently
    kills the listener."""
    from agent_utilities.messaging.models import MessagingConfig

    from graph_os.messaging.backends.telegram import TelegramBackend

    conflict_cls = fake_telegram
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_BASE_S", "0.01")
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_MAX_S", "0.02")

    backend = TelegramBackend(MessagingConfig(token="1:x"))
    backend._connected = True

    calls: list[int] = []
    event = _msg_event()

    async def _listen() -> AsyncIterator[Any]:
        calls.append(1)
        if len(calls) == 1:
            raise conflict_cls("409 restart race")
        yield event

    # Replace listen() with the scripted async generator (no real Telegram app needed).
    monkeypatch.setattr(backend, "listen", _listen)

    router = InboundRouter()
    router._running = True

    received: list[Any] = []

    async def handler(ev: Any, _b: Any) -> None:
        received.append(ev)
        router._running = False

    router.set_default_handler(handler)

    await asyncio.wait_for(router._supervise_backend(backend), timeout=5)

    assert len(calls) == 2  # Conflict on attempt 1, restarted, served on attempt 2
    assert received == [event]
