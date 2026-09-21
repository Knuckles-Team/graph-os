"""Authority-side persistence port for control-plane projection."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import AtomicCommitReceipt, AuthoritativeMutation, OutboxEnvelope

__all__ = ["AtomicAuthorityRepository"]


@runtime_checkable
class AtomicAuthorityRepository(Protocol):
    """The sole persistence seam for an authoritative mutation.

    ``commit_authority_and_outbox`` is one transaction from the adapter's
    point of view: the authoritative row and its event are committed
    together, or neither is visible.  ``event`` is ``None`` only for a
    rollback, which is a local authority action and intentionally emits no
    projection event.
    """

    def commit_authority_and_outbox(
        self,
        mutation: AuthoritativeMutation,
        event: OutboxEnvelope | None,
    ) -> AtomicCommitReceipt:
        """Atomically commit authority and its optional outbox event."""
