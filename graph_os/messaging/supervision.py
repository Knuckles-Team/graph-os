"""Typed messaging channel supervision state machine.

CONCEPT:GRAPHOS-MESSAGING-R001.1 — typed supervision states, their legal
transition table, and refusal of any transition not in that table.

This module is the sole authority for what counts as a legal move in a
supervised channel's lifecycle (``graph_os.messaging.router.InboundRouter``
and ``graph_os.messaging.registry.MessagingRegistry``). It has no asyncio,
no backend import, and no I/O: a later slice (``GRAPHOS-MESSAGING-R002``
through ``R005``) wires the router and registry to call :func:`transition`
instead of mutating ad hoc booleans.

Org boundary (GRAPHOS-MESSAGING-R006): GraphOS owns this supervision model.
agent-utilities owns the adapter contract (``agent_utilities.messaging.base
.MessagingBackend``) this model supervises; this module imports nothing from
agent-utilities.
"""

from __future__ import annotations

from enum import Enum


class ChannelSupervisionState(Enum):
    """The closed set of states a supervised messaging channel can be in."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKING_OFF = "backing_off"
    DEGRADED = "degraded"
    STOPPING = "stopping"


class IllegalSupervisionTransition(Exception):
    """Raised when a supervision transition is not in the legal table."""

    def __init__(
        self, current: ChannelSupervisionState, target: ChannelSupervisionState
    ) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"illegal channel supervision transition: {current.value} -> {target.value}"
        )


# The fixed legal-transition table. A pair not present here is refused.
_TRANSITIONS: dict[ChannelSupervisionState, frozenset[ChannelSupervisionState]] = {
    ChannelSupervisionState.STOPPED: frozenset({ChannelSupervisionState.STARTING}),
    ChannelSupervisionState.STARTING: frozenset(
        {
            ChannelSupervisionState.RUNNING,
            ChannelSupervisionState.STOPPED,
            ChannelSupervisionState.DEGRADED,
        }
    ),
    ChannelSupervisionState.RUNNING: frozenset(
        {
            ChannelSupervisionState.BACKING_OFF,
            ChannelSupervisionState.STOPPING,
        }
    ),
    ChannelSupervisionState.BACKING_OFF: frozenset(
        {
            ChannelSupervisionState.RUNNING,
            ChannelSupervisionState.STOPPING,
        }
    ),
    ChannelSupervisionState.DEGRADED: frozenset({ChannelSupervisionState.STOPPING}),
    ChannelSupervisionState.STOPPING: frozenset({ChannelSupervisionState.STOPPED}),
}


def legal_targets(
    current: ChannelSupervisionState,
) -> frozenset[ChannelSupervisionState]:
    """The set of states ``current`` may legally move to."""
    return _TRANSITIONS.get(current, frozenset())


def transition(
    current: ChannelSupervisionState, target: ChannelSupervisionState
) -> ChannelSupervisionState:
    """Move from ``current`` to ``target``, or refuse an illegal move.

    Args:
        current: The channel's current supervision state.
        target: The requested next state.

    Returns:
        ``target``, when the move is legal.

    Raises:
        IllegalSupervisionTransition: when ``target`` is not a legal
            successor of ``current`` per the fixed transition table.
    """
    if target not in legal_targets(current):
        raise IllegalSupervisionTransition(current, target)
    return target
