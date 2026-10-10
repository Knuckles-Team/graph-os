"""Typed channel supervision state machine (GRAPHOS-MESSAGING-R001.1).

Covers every legal transition in the fixed table and a representative set
of illegal ones, each of which must refuse via ``IllegalSupervisionTransition``
rather than silently applying or ignoring the move.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

from graph_os.messaging.router import InboundRouter
from graph_os.messaging.supervision import (
    _TRANSITIONS,
    ChannelSupervisionState,
    IllegalSupervisionTransition,
    legal_targets,
    transition,
)

from ._fakes import FakeMessagingBackend

_ALL_LEGAL_PAIRS = [
    (current, target) for current, targets in _TRANSITIONS.items() for target in targets
]

_ILLEGAL_PAIRS = [
    (current, target)
    for current, target in itertools.product(ChannelSupervisionState, repeat=2)
    if target not in legal_targets(current)
]


@pytest.mark.parametrize("current,target", _ALL_LEGAL_PAIRS)
def test_legal_transition_succeeds(
    current: ChannelSupervisionState, target: ChannelSupervisionState
) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(
    "current,target",
    [
        (ChannelSupervisionState.STOPPED, ChannelSupervisionState.RUNNING),
        (ChannelSupervisionState.DEGRADED, ChannelSupervisionState.RUNNING),
        (ChannelSupervisionState.STOPPED, ChannelSupervisionState.BACKING_OFF),
        (ChannelSupervisionState.STOPPING, ChannelSupervisionState.RUNNING),
        (ChannelSupervisionState.RUNNING, ChannelSupervisionState.STARTING),
    ],
)
def test_illegal_transition_refuses(
    current: ChannelSupervisionState, target: ChannelSupervisionState
) -> None:
    with pytest.raises(IllegalSupervisionTransition) as exc_info:
        transition(current, target)
    message = str(exc_info.value)
    assert current.value in message
    assert target.value in message
    assert exc_info.value.current is current
    assert exc_info.value.target is target


def test_illegal_pairs_is_the_complement_of_the_table() -> None:
    """Every (current, target) pair not in the table is provably illegal,
    and the table is not vacuously empty for any reachable state."""
    assert _ILLEGAL_PAIRS, "expected at least one illegal pair to exist"
    for current, target in _ILLEGAL_PAIRS:
        with pytest.raises(IllegalSupervisionTransition):
            transition(current, target)


def test_every_state_has_a_defined_row() -> None:
    for state in ChannelSupervisionState:
        assert state in _TRANSITIONS, f"{state} is missing a transition row"


def test_stopped_has_no_incoming_self_loop() -> None:
    assert ChannelSupervisionState.STOPPED not in legal_targets(
        ChannelSupervisionState.STOPPED
    )


@pytest.mark.asyncio
async def test_router_promotes_connected_backend_to_running_then_drains_to_stopped() -> (
    None
):
    """GRAPHOS-MESSAGING-R002/R004 minimal wiring: a connected backend reaches
    RUNNING on start() and STOPPED (never skipping STOPPING) on stop()."""
    router = InboundRouter()
    backend = FakeMessagingBackend("telegram")
    router.register_backend(backend)
    assert router.state_of("telegram") is ChannelSupervisionState.STOPPED

    start_task = asyncio.create_task(router.start())
    await asyncio.sleep(0)
    assert router.state_of("telegram") is ChannelSupervisionState.RUNNING

    await router.stop()
    assert router.state_of("telegram") is ChannelSupervisionState.STOPPED
    await asyncio.wait_for(start_task, timeout=1)


@pytest.mark.asyncio
async def test_router_never_promotes_an_unconnected_backend_to_running() -> None:
    """GRAPHOS-MESSAGING-R002: an unconnected backend is skipped, not RUNNING."""
    router = InboundRouter()
    backend = FakeMessagingBackend("mattermost")
    backend._connected = False
    router.register_backend(backend)

    start_task = asyncio.create_task(router.start())
    await asyncio.sleep(0)
    assert router.state_of("mattermost") is ChannelSupervisionState.STOPPED

    await router.stop()
    await asyncio.wait_for(start_task, timeout=1)


class _AlwaysFailingBackend:
    """Fixture backend whose ``listen()`` always raises immediately."""

    id = "telegram"
    is_connected = True

    async def listen(self):  # noqa: ANN201 - async generator, matches MessagingBackend.listen
        raise RuntimeError("simulated short-lived listener failure")
        yield  # pragma: no cover - unreachable, makes this an async generator


class _OneHealthyRunThenFailingBackend:
    """Fixture backend: attempt 1 fails immediately, attempt 2 runs for a
    while (a simulated healthy run) before failing, attempt 3 fails
    immediately again."""

    id = "telegram"
    is_connected = True

    def __init__(self) -> None:
        self.calls = 0

    async def listen(self):  # noqa: ANN201
        self.calls += 1
        if self.calls == 2:
            await asyncio.sleep(0.05)
        raise RuntimeError(f"simulated failure #{self.calls}")
        yield  # pragma: no cover - unreachable, makes this an async generator


@pytest.mark.spec("GRAPHOS-MESSAGING-R003")
@pytest.mark.asyncio
async def test_backoff_delay_sequence_is_monotonically_bounded_by_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHOS-MESSAGING-R003: a sequence of simulated short-lived failures
    produces a delay sequence that doubles each time and never exceeds
    ``MESSAGING_LISTEN_BACKOFF_MAX_S``."""
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_BASE_S", "0.01")
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_MAX_S", "0.04")
    monkeypatch.setenv("MESSAGING_LISTEN_HEALTHY_RESET_S", "999")

    router = InboundRouter()
    router._running = True
    backend = _AlwaysFailingBackend()
    delays: list[float] = []

    def fake_record(backend_id: str, delay: float, ran_for: float) -> None:
        delays.append(delay)
        if len(delays) >= 5:
            router._running = False

    monkeypatch.setattr(router, "_record_listener_restart", fake_record)

    await asyncio.wait_for(router._supervise_backend(backend), timeout=5)

    assert len(delays) == 5
    assert delays[0] == pytest.approx(0.01)
    for prev, nxt in zip(delays, delays[1:], strict=False):
        assert nxt >= prev - 1e-9, delays
    assert max(delays) == pytest.approx(0.04)
    assert delays[-1] == pytest.approx(0.04)


@pytest.mark.spec("GRAPHOS-MESSAGING-R003")
@pytest.mark.asyncio
async def test_backoff_resets_to_base_after_a_sustained_healthy_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHOS-MESSAGING-R003: one long-lived healthy run followed by a
    failure resets the backoff delay to base rather than continuing to
    double."""
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_BASE_S", "0.01")
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_MAX_S", "0.05")
    monkeypatch.setenv("MESSAGING_LISTEN_HEALTHY_RESET_S", "0.03")

    router = InboundRouter()
    router._running = True
    backend = _OneHealthyRunThenFailingBackend()
    recorded: list[tuple[float, float]] = []

    def fake_record(backend_id: str, delay: float, ran_for: float) -> None:
        recorded.append((delay, ran_for))
        if len(recorded) >= 3:
            router._running = False

    monkeypatch.setattr(router, "_record_listener_restart", fake_record)

    await asyncio.wait_for(router._supervise_backend(backend), timeout=5)

    delays = [d for d, _ in recorded]
    assert len(delays) == 3
    assert delays[0] == pytest.approx(0.01)  # base, attempt 1 (short fail)
    assert delays[1] == pytest.approx(0.02)  # doubled after attempt 1
    # attempt 2 ran long enough to count as a sustained healthy run
    assert recorded[1][1] >= 0.03
    assert delays[2] == pytest.approx(0.01)  # reset to base after the healthy run


@pytest.mark.spec("GRAPHOS-MESSAGING-R004")
@pytest.mark.asyncio
async def test_stop_during_backoff_wait_cancels_cleanly_with_no_pending_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GRAPHOS-MESSAGING-R004: cancelling a router while a backend is
    mid backoff-wait is a clean shutdown — stop() leaves no restart task
    behind and the task collection is empty."""
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_BASE_S", "30")
    monkeypatch.setenv("MESSAGING_LISTEN_BACKOFF_MAX_S", "30")

    router = InboundRouter()
    backend = _AlwaysFailingBackend()
    router.register_backend(backend)

    start_task = asyncio.create_task(router.start())
    # Let start() create the supervisor task, run its one failing attempt,
    # and enter the (long) backoff sleep.
    for _ in range(5):
        await asyncio.sleep(0)

    await router.stop()

    assert router._tasks == []
    assert router.state_of("telegram") is ChannelSupervisionState.STOPPED
    await asyncio.wait_for(start_task, timeout=1)
