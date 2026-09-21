"""Validation helpers and errors for control-plane projection repositories.

The persistence interfaces live in :mod:`.protocols`; they are re-exported
here for compatibility with existing adapters.  This module owns only the
contract validation and authority commit helpers used by the projector.
"""

from __future__ import annotations

from .authority import AtomicAuthorityRepository
from .models import (
    AtomicCommitReceipt,
    AuthoritativeMutation,
    OutboxEnvelope,
)
from .observability import DriftSink
from .protocols import (
    CheckpointStore,
    GraphOSProjection,
    OutboxReader,
    TombstoneReader,
)

__all__ = [
    "AtomicAuthorityRepository",
    "CheckpointConflict",
    "CheckpointStore",
    "GraphOSProjection",
    "DriftSink",
    "OutboxReader",
    "ProjectionContractError",
    "ProjectionRepositoryUnavailable",
    "ReverseSyncRejected",
    "TombstoneReader",
    "commit_authoritative_change",
    "reject_reverse_sync",
]


class ProjectionContractError(ValueError):
    """A caller or adapter violated the projection contract."""


class ProjectionRepositoryUnavailable(RuntimeError):
    """An authority, outbox, checkpoint or GraphOS adapter is unavailable."""


class CheckpointConflict(ProjectionContractError):
    """A checkpoint CAS or fencing precondition failed."""


class ReverseSyncRejected(ProjectionContractError):
    """GraphOS projection data cannot become authority through reverse sync."""


def reject_reverse_sync(*, source: str) -> None:
    """Reject an attempted GraphOS-to-authority write before any adapter call."""

    del source
    raise ReverseSyncRejected("reverse_sync_is_not_an_authority_path")


def _validate_event_matches_mutation(
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope,
) -> None:
    expected_operation = "tombstone" if mutation.operation == "delete" else "upsert"
    if event.operation != expected_operation:
        raise ProjectionContractError("event_operation_does_not_match_mutation")
    if (
        event.aggregate_type != mutation.aggregate_type
        or event.aggregate_id != mutation.aggregate_id
        or event.sequence != mutation.sequence
        or event.authoritative_revision != mutation.authoritative_revision
    ):
        raise ProjectionContractError("event_identity_does_not_match_mutation")
    if event.payload_digest != mutation.change_digest:
        raise ProjectionContractError("event_payload_digest_does_not_match_mutation")
    if event.aggregate_digest != mutation.aggregate_digest:
        raise ProjectionContractError("event_aggregate_digest_does_not_match_mutation")
    if event.summary != mutation.summary:
        raise ProjectionContractError("event_summary_does_not_match_mutation")


def commit_authoritative_change(
    repository: AtomicAuthorityRepository,
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope | None,
) -> AtomicCommitReceipt:
    """Commit authority and outbox once, preserving rollback/no-event semantics.

    This helper has no retry loop and no fallback repository.  The caller's
    adapter owns transactionality and may return ``replayed=True`` when the
    exact mutation/event was already committed after a crash.
    """

    if mutation.origin != "authoritative":
        raise ProjectionContractError("observation_promotion_requires_policy_path")
    return _commit_validated_change(repository, mutation, event)


def _commit_validated_change(
    repository: AtomicAuthorityRepository,
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope | None,
) -> AtomicCommitReceipt:
    """Commit an already-policy-validated mutation through the one adapter seam."""

    _validate_commit_event(mutation, event)
    receipt = _commit_authority_and_outbox(repository, mutation, event)
    _validate_commit_receipt(receipt, mutation, event)
    return receipt


def _validate_commit_event(
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope | None,
) -> None:
    if mutation.operation == "rollback":
        if event is not None:
            raise ProjectionContractError("rollback_must_not_emit_event")
    elif event is None:
        raise ProjectionContractError("forward_mutation_requires_outbox_event")
    else:
        _validate_event_matches_mutation(mutation, event)


def _commit_authority_and_outbox(
    repository: AtomicAuthorityRepository,
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope | None,
) -> AtomicCommitReceipt:
    try:
        return repository.commit_authority_and_outbox(mutation, event)
    except ReverseSyncRejected:
        raise
    except ProjectionContractError:
        raise
    except ProjectionRepositoryUnavailable:
        raise
    except Exception as exc:
        raise ProjectionRepositoryUnavailable(
            "authority_transaction_unavailable"
        ) from exc


def _validate_commit_receipt(
    receipt: AtomicCommitReceipt,
    mutation: AuthoritativeMutation,
    event: OutboxEnvelope | None,
) -> None:
    if receipt.mutation_id != mutation.mutation_id:
        raise ProjectionContractError("authority_receipt_mutation_mismatch")
    if (
        receipt.aggregate_type != mutation.aggregate_type
        or receipt.aggregate_id != mutation.aggregate_id
        or receipt.sequence != mutation.sequence
        or receipt.authoritative_revision != mutation.authoritative_revision
    ):
        raise ProjectionContractError("authority_receipt_identity_mismatch")
    if event is None and receipt.event_id is not None:
        raise ProjectionContractError("rollback_receipt_contains_event")
    if event is not None and receipt.event_id != event.event_id:
        raise ProjectionContractError("authority_receipt_event_mismatch")
