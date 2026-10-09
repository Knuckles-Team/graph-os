"""In-process read/config facade over the pure AIMD controller state.

Part of GRAPHOS-CAPACITY-R002 and the GRAPHOS-CAPACITY-R001 build sequence:
holds the latest recorded
:class:`~graph_os.fleet.error_budget.ThrottleDecision` and the selected
:class:`~graph_os.fleet.error_budget.ThrottleMode` per partition, so a
hosted operation (``capacity.throttle.status`` / ``capacity.throttle.
set_mode`` in :mod:`graph_os.api.ops.capacity`) can read or administer
controller state without a second fleet call path or a cloned engine
capacity model.

This module never calls a child or the engine itself. Recording a decision
is the job of the limiter that will sit at a real fleet dispatch point
(GRAPHOS-CAPACITY-R001.2, not yet built); this facade only stores and
reports what it is given. A partition with no recorded decision yet reports
an explicit ``no_decision_recorded`` reason and no limit, never a fabricated
healthy status.
"""

from __future__ import annotations

from dataclasses import dataclass

from graph_os.fleet.error_budget import Partition, ThrottleDecision, ThrottleMode

_NO_DECISION_REASON = "no_decision_recorded"


@dataclass(frozen=True, slots=True)
class ThrottleStatus:
    """A caller-visible snapshot of one partition's throttle state."""

    partition: Partition
    mode: ThrottleMode
    current_limit: int | None
    reason: str
    last_decision: ThrottleDecision | None


class ThrottleRegistry:
    """Bounded in-memory per-partition throttle state.

    Not durable and not an authority on its own: callers reach it only
    through the hosted capacity operations (scope- and audit-checked by the
    shared invoke pipeline) or a bound limiter, never directly.
    """

    def __init__(self, *, default_mode: ThrottleMode = ThrottleMode.OBSERVE) -> None:
        self._default_mode = default_mode
        self._modes: dict[tuple[str, str, str, str], ThrottleMode] = {}
        self._decisions: dict[tuple[str, str, str, str], ThrottleDecision] = {}

    def record_decision(self, decision: ThrottleDecision) -> None:
        """Store the latest decision for its partition, replacing any prior one."""
        self._decisions[decision.partition.key()] = decision

    def set_mode(self, partition: Partition, mode: ThrottleMode) -> None:
        """Select observe or enforce mode for one partition."""
        self._modes[partition.key()] = mode

    def mode(self, partition: Partition) -> ThrottleMode:
        return self._modes.get(partition.key(), self._default_mode)

    def status(self, partition: Partition) -> ThrottleStatus:
        decision = self._decisions.get(partition.key())
        mode = self.mode(partition)
        if decision is None:
            return ThrottleStatus(
                partition=partition,
                mode=mode,
                current_limit=None,
                reason=_NO_DECISION_REASON,
                last_decision=None,
            )
        return ThrottleStatus(
            partition=partition,
            mode=mode,
            current_limit=decision.new_limit,
            reason=decision.reason,
            last_decision=decision,
        )


__all__ = ["ThrottleRegistry", "ThrottleStatus"]
