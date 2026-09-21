"""Typed persistence seams for control-plane projection.

The projection service depends on these small interfaces rather than on a
specific relational or GraphOS adapter.  Authority, outbox, checkpoint, and
discrepancy operations remain separate so an adapter cannot accidentally
create a second write path or advance a cursor before a projection succeeds.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    GraphProjectionReceipt,
    OutboxEnvelope,
    ProjectionCheckpoint,
    ProjectionScope,
)

__all__ = [
    "CheckpointStore",
    "GraphOSProjection",
    "OutboxReader",
    "TombstoneReader",
]


@runtime_checkable
class OutboxReader(Protocol):
    """Bounded keyset reader over immutable events for one aggregate scope."""

    def read_after(
        self,
        scope: ProjectionScope,
        after_sequence: int,
        limit: int,
    ) -> tuple[OutboxEnvelope, ...]:
        """Return at most ``limit`` events strictly after ``after_sequence``."""


@runtime_checkable
class TombstoneReader(Protocol):
    """Bounded reader for already-projected tombstones eligible for cleanup."""

    def read_tombstones_before(
        self,
        scope: ProjectionScope,
        before_sequence: int,
        limit: int,
    ) -> tuple[OutboxEnvelope, ...]:
        """Return tombstones in ascending sequence order, never raw records."""


@runtime_checkable
class CheckpointStore(Protocol):
    """Durable fenced keyset checkpoint authority."""

    def read_checkpoint(self, scope: ProjectionScope) -> ProjectionCheckpoint | None:
        """Read one checkpoint or ``None`` before the first event."""

    def save_checkpoint(
        self,
        scope: ProjectionScope,
        expected: ProjectionCheckpoint | None,
        checkpoint: ProjectionCheckpoint,
    ) -> None:
        """CAS-save a checkpoint and reject stale fence/expected state."""

    def reset_checkpoint(self, scope: ProjectionScope, fence_token: int) -> None:
        """Reset one projection cursor under the current fencing term."""


@runtime_checkable
class GraphOSProjection(Protocol):
    """GraphOS write projection with event-id idempotency."""

    def apply_event(
        self, event: OutboxEnvelope, fence_token: int
    ) -> GraphProjectionReceipt:
        """Apply one event, returning a replay receipt for an existing event ID."""

    def reset_projection(self, scope: ProjectionScope, fence_token: int) -> None:
        """Clear only this projection scope for deterministic rebuild."""

    def cleanup_tombstone(self, event: OutboxEnvelope, fence_token: int) -> None:
        """Remove one previously projected tombstone under the same fence."""
