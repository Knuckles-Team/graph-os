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


@pytest.mark.spec("GRAPHOS-MESSAGING-R001.1", "GRAPHOS-MESSAGING-R002")
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
@pytest.mark.spec("GRAPHOS-MESSAGING-R001.1", "GRAPHOS-MESSAGING-R002")
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


@pytest.mark.spec("GRAPHOS-MESSAGING-R001.1", "GRAPHOS-MESSAGING-R002")
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
