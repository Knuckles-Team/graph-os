"""One-run/one-native-WorkItem admission protocol."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from graph_os.control_plane.policy.models import canonical_digest

from .audit import AuditChain, AuditDiscontinuityError
from .models import (
    AdmissionReceipt,
    NativeAdmissionRequest,
    RunRecord,
)

__all__ = [
    "InMemoryNativeAdmission",
    "NativeAdmissionError",
    "NativeWorkItemAdmissionProtocol",
    "ResolvedAuthorizationVerifier",
    "ReplayDriftError",
]


class NativeAdmissionError(ValueError):
    """The native WorkItem admission precondition failed."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


class ReplayDriftError(NativeAdmissionError):
    """A duplicate identity arrived with a different body or resolution."""


@runtime_checkable
class NativeWorkItemAdmissionProtocol(Protocol):
    """The only execution-plane seam exposed by this domain core."""

    def admit_once(self, request: NativeAdmissionRequest) -> AdmissionReceipt:
        """Atomically admit one resolved run and one native WorkItem."""


class ResolvedAuthorizationVerifier(Protocol):
    def verify_authorization(
        self, authorization: object, *, now: int | None = None
    ) -> None:
        """Verify the exact persisted policy authorization."""


class InMemoryNativeAdmission:
    """Reference implementation with atomic duplicate/concurrency behavior.

    It stores the exact resolved version and never re-resolves a policy during
    duplicate delivery or recovery.  It deliberately exposes no claim, lease,
    fencing, completion, or result methods; the native engine owns those.
    """

    def __init__(
        self,
        *,
        audit: AuditChain | None = None,
        clock: Callable[[], int] | None = None,
        authorization_verifier: ResolvedAuthorizationVerifier | None = None,
    ) -> None:
        self.audit = audit
        self.clock = clock or (lambda: 0)
        self.authorization_verifier = authorization_verifier
        self._lock = threading.RLock()
        self._records: dict[str, RunRecord] = {}
        self._work_items: dict[str, str] = {}

    def admit_once(self, request: NativeAdmissionRequest) -> AdmissionReceipt:
        with self._lock:
            run_id = request.resolution.run_id
            work_item_id = request.work_item.work_item_id
            replay = self._replay_receipt(run_id, request)
            if replay is not None:
                return replay

            self._ensure_work_item_available(work_item_id)
            self._verify_authorization(request)
            self._ensure_audit_capacity()
            now = self.clock()
            audit_ids = self._append_audit(request, now=now)

            record = RunRecord(
                resolution=request.resolution,
                work_item=request.work_item,
                audit_event_ids=tuple(audit_ids),
            )
            self._records[run_id] = record
            self._work_items[work_item_id] = run_id
            return self._receipt(record, created=True)

    def _replay_receipt(
        self, run_id: str, request: NativeAdmissionRequest
    ) -> AdmissionReceipt | None:
        existing = self._records.get(run_id)
        if existing is None:
            return None
        if (
            existing.resolution != request.resolution
            or existing.work_item != request.work_item
        ):
            raise ReplayDriftError("replay_or_body_drift")
        return self._receipt(existing, created=False)

    def _ensure_work_item_available(self, work_item_id: str) -> None:
        if self._work_items.get(work_item_id) is not None:
            raise NativeAdmissionError("work_item_identity_conflict")

    def _verify_authorization(self, request: NativeAdmissionRequest) -> None:
        if self.authorization_verifier is None:
            return
        try:
            self.authorization_verifier.verify_authorization(
                request.resolution.authorization,
                now=self.clock(),
            )
        except Exception as exc:  # fail closed at the admission boundary
            raise NativeAdmissionError("authorization_stale_or_invalid") from exc

    def _ensure_audit_capacity(self) -> None:
        if self.audit is None:
            return
        try:
            self.audit.require_valid()
        except AuditDiscontinuityError as exc:
            raise NativeAdmissionError("audit_discontinuity") from exc
        if not self.audit.can_append(2):
            raise NativeAdmissionError("audit_capacity_exceeded")

    def _append_audit(self, request: NativeAdmissionRequest, *, now: int) -> list[str]:
        if self.audit is None:
            return []
        run_event = self.audit.append(
            kind="run.admitted",
            subject_ref=request.resolution.run_id,
            payload_digest=request.resolution.resolution_digest,
            observed_at=now,
        )
        item_event = self.audit.append(
            kind="work_item.admitted",
            subject_ref=request.work_item.work_item_id,
            payload_digest=canonical_digest(request.work_item),
            observed_at=now,
        )
        return [run_event.event_id, item_event.event_id]

    def read(self, run_id: str) -> RunRecord | None:
        """Return the exact persisted resolution for crash/reclaim recovery."""

        with self._lock:
            return self._records.get(run_id)

    def _receipt(self, record: RunRecord, *, created: bool) -> AdmissionReceipt:
        return AdmissionReceipt(
            run_id=record.resolution.run_id,
            work_item_id=record.work_item.work_item_id,
            resolution_digest=record.resolution.resolution_digest,
            work_item_digest=canonical_digest(record.work_item),
            created=created,
        )
