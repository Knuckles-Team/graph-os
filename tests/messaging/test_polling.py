"""Regression: one platform's lease loss must not stop another platform's
inbound listener.

Previously the AU daemon ran EVERY backend's
listener under the ONE ``_serve`` asyncio task and cancelled that whole task
off the single shared ``stop_event`` — so
``graph_os/messaging/lease.py::run_with_intake_leases`` setting that event on
ANY platform's lease loss silently killed every OTHER healthy platform's
inbound polling too (this is what took Telegram inbound down in production
when only the Mattermost lease was lost).

This drives the REAL ``run_poll_loop`` — its own event loop, its own
per-platform lease observer, and the real ``_drop_platform`` task-name
lookup-and-cancel closure — and substitutes only ``_serve``'s backend/router
construction (irrelevant to this bug) with a fake that exposes named,
long-lived tasks through the same ``router_box`` handoff the real ``_serve``
populates. The seam under test — the per-platform ``threading.Event`` →
single-task cancellation path — is never
mocked.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from agent_utilities.messaging import commands
from agent_utilities.messaging import router as au_messaging_router

from graph_os.messaging import polling
from graph_os.messaging import router as graphos_router
from graph_os.messaging.service import MessagingService


class _FakeRouter:
    """Mimics just the sliver of ``InboundRouter`` ``_drop_platform`` reads."""

    def __init__(self, tasks: list[asyncio.Task[Any]]) -> None:
        self._tasks = tasks


def test_graphos_poller_connects_backend_and_au_command_handler(monkeypatch) -> None:
    seen: list[Any] = []

    class FakeBackend:
        async def register_commands(self, specs: Any) -> None:
            seen.append(("commands", specs))

    fake_backend = FakeBackend()

    class Service:
        async def get_backend(self, platform: str) -> Any:
            seen.append(("backend", platform))
            return fake_backend

        def register_connected(self, connected: Any) -> None:
            seen.append(("connected", connected))

    class FakeRouter:
        def register_backend(self, connected: Any) -> None:
            seen.append(("registered", connected))

        def set_default_handler(self, handler: Any) -> None:
            seen.append(("handler", handler))

        async def start(self) -> None:
            seen.append("started")

    handler = object()

    async def make_handler(_engine: Any) -> Any:
        return handler

    monkeypatch.setattr(MessagingService, "instance", lambda _engine: Service())
    monkeypatch.setattr(graphos_router, "InboundRouter", FakeRouter)
    monkeypatch.setattr(au_messaging_router, "create_planner_handler", make_handler)
    monkeypatch.setattr(commands, "command_specs", lambda _kind: ["help"])

    box: dict[str, Any] = {}
    asyncio.run(polling._serve(object(), ["telegram"], box))

    assert isinstance(box["router"], FakeRouter)
    assert seen == [
        ("backend", "telegram"),
        ("connected", fake_backend),
        ("commands", ["help"]),
        ("registered", fake_backend),
        ("handler", handler),
        "started",
    ]


def test_dropping_one_platform_leaves_the_other_listener_running(monkeypatch) -> None:
    ready = threading.Event()
    state: dict[str, dict[str, asyncio.Task[Any]]] = {}

    async def _fake_serve(
        engine: Any, platforms: list[str], router_box: dict[str, Any]
    ) -> None:
        tasks = {
            platform: asyncio.create_task(
                asyncio.sleep(3600), name=f"messaging-router-{platform}"
            )
            for platform in platforms
        }
        state["tasks"] = tasks
        router_box["router"] = _FakeRouter(list(tasks.values()))
        ready.set()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    monkeypatch.setattr(polling, "_serve", _fake_serve)

    stop_event = threading.Event()
    platform_stop_events = {
        "mattermost": threading.Event(),
        "telegram": threading.Event(),
    }

    thread = threading.Thread(
        target=polling.run_poll_loop,
        args=(
            object(),
            ["mattermost", "telegram"],
            stop_event,
            platform_stop_events,
        ),
        daemon=True,
        name="test-run-poll-loop",
    )
    thread.start()
    try:
        assert ready.wait(timeout=2.0)
        tasks = state["tasks"]

        # Only mattermost's lease is lost.
        platform_stop_events["mattermost"].set()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not tasks["mattermost"].done():
            time.sleep(0.02)

        assert tasks["mattermost"].done(), (
            "mattermost's listener task must be cancelled once its "
            "platform_stop_event fires"
        )
        # Give a cancellation-that-shouldn't-happen a fair chance to show up.
        time.sleep(0.2)
        assert not tasks["telegram"].done(), (
            "telegram's listener task must NOT be touched by mattermost's lease loss"
        )
        assert not stop_event.is_set(), (
            "losing one of two leases must not request the full daemon stop"
        )
    finally:
        stop_event.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
