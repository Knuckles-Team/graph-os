"""All-or-nothing run capacity acquisition with a one-re-decision contract.

Standalone primitive for GRAPHOS-FLEET-R009.1: a bounded pool of capacity
slots that a run holder acquires atomically (all requested units grant, or
none do), with exactly one re-decision allowed after a denial, and release
of held capacity back to the pool when the holding run stops. This module
has no dependency on `admission.py`, `graph_os/a2a/routing.py`, or
`graph_os/a2a/composition.py`; those wire against it separately
(GRAPHOS-FLEET-R009.2 / R009.3).
"""

from __future__ import annotations

import threading

__all__ = [
    "CapacityDecision",
    "CapacityError",
    "CapacityLedger",
    "CapacityReDecisionExhaustedError",
]


class CapacityError(ValueError):
    """A capacity request could not be granted."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


class CapacityReDecisionExhaustedError(CapacityError):
    """A holder already used its one re-decision and was denied again."""

    def __init__(self, holder_id: str) -> None:
        super().__init__(
            "capacity_redecision_exhausted",
            f"holder {holder_id!r} already re-decided once",
        )


class CapacityDecision:
    """The outcome of one `CapacityLedger.acquire` call."""

    __slots__ = ("granted", "amount", "redecision")

    def __init__(self, *, granted: bool, amount: int, redecision: bool) -> None:
        self.granted = granted
        self.amount = amount
        self.redecision = redecision


class CapacityLedger:
    """A fixed-size pool of capacity slots acquired all-or-nothing per holder.

    - `acquire` grants the full requested amount or none of it.
    - A denial is remembered per holder; the holder's *next* `acquire` call
      is its one allowed re-decision. A second denial on that re-decision
      raises `CapacityReDecisionExhaustedError` instead of granting another
      retry.
    - `release` returns a holder's held capacity to the pool and clears its
      re-decision state, so the holder (or its run id, if reused) starts
      clean on its next acquisition.
    """

    def __init__(self, total: int) -> None:
        if total < 0:
            raise CapacityError("capacity_total_invalid", f"total={total!r}")
        self._total = total
        self._available = total
        self._held: dict[str, int] = {}
        self._pending_redecision: set[str] = set()
        self._exhausted: set[str] = set()
        self._lock = threading.RLock()

    @property
    def total(self) -> int:
        return self._total

    @property
    def available(self) -> int:
        with self._lock:
            return self._available

    def held_by(self, holder_id: str) -> int:
        with self._lock:
            return self._held.get(holder_id, 0)

    def acquire(self, holder_id: str, amount: int = 1) -> CapacityDecision:
        """Request `amount` slots for `holder_id`, all-or-nothing.

        Raises `CapacityReDecisionExhaustedError` if this holder already
        used its one re-decision and was denied again; a holder reaches
        that state only after this method itself raised it once, and
        stays there until `release` clears it.
        """
        if amount <= 0:
            raise CapacityError("capacity_amount_invalid", f"amount={amount!r}")
        with self._lock:
            if holder_id in self._exhausted:
                raise CapacityReDecisionExhaustedError(holder_id)
            is_redecision = holder_id in self._pending_redecision
            if amount <= self._available:
                self._available -= amount
                self._held[holder_id] = self._held.get(holder_id, 0) + amount
                self._pending_redecision.discard(holder_id)
                return CapacityDecision(
                    granted=True, amount=amount, redecision=is_redecision
                )
            if is_redecision:
                self._pending_redecision.discard(holder_id)
                self._exhausted.add(holder_id)
                raise CapacityReDecisionExhaustedError(holder_id)
            self._pending_redecision.add(holder_id)
            return CapacityDecision(granted=False, amount=0, redecision=False)

    def release(self, holder_id: str) -> int:
        """Return `holder_id`'s held capacity to the pool. Idempotent.

        Also clears any pending-re-decision or exhausted state for this
        holder, so a stopped run's identifier can acquire cleanly again.
        Returns the amount released (0 if the holder held nothing).
        """
        with self._lock:
            amount = self._held.pop(holder_id, 0)
            self._available += amount
            self._pending_redecision.discard(holder_id)
            self._exhausted.discard(holder_id)
            return amount
