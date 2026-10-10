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
    "CapacityAcquisition",
    "CapacityDeniedError",
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


class CapacityDeniedError(NativeAdmissionError):
    """A candidate run was denied capacity after its one re-decision."""

    def __init__(self, run_id: str) -> None:
        super().__init__("capacity_denied_after_redecision", run_id)


class CapacityAcquisition:
    """All-or-nothing capacity acquisition with one re-decision on denial.

    GRAPHOS-FLEET-R009.1: a candidate run acquires every requested resource
    unit or none at all. The first denial consumes that run's single
    re-decision and returns ``False`` so the caller may retry once against a
    revised resource set; a second denial for the same run fails closed with
    :class:`CapacityDeniedError`. A stopped (or re-acquiring) run releases
    every unit it is currently holding before anything else is evaluated.
    """

    def __init__(self, *, capacity: dict[str, int]) -> None:
        self._lock = threading.RLock()
        self._capacity: dict[str, int] = dict(capacity)
        self._held: dict[str, dict[str, int]] = {}
        self._redecided: set[str] = set()

    def acquire(self, run_id: str, resources: dict[str, int]) -> bool:
        """Acquire ``resources`` for ``run_id``, all-or-nothing."""

        with self._lock:
            if self._can_satisfy(resources):
                self._commit(run_id, resources)
                self._redecided.discard(run_id)
                return True
            if run_id in self._redecided:
                raise CapacityDeniedError(run_id)
            self._redecided.add(run_id)
            return False

    def release(self, run_id: str) -> None:
        """Release every resource unit ``run_id`` currently holds (stop)."""

        with self._lock:
            held = self._held.pop(run_id, None)
            self._redecided.discard(run_id)
            if held is None:
                return
            for key, units in held.items():
                self._capacity[key] = self._capacity.get(key, 0) + units

    def held(self, run_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._held.get(run_id, {}))

    def _can_satisfy(self, resources: dict[str, int]) -> bool:
        return all(self._capacity.get(key, 0) >= units for key, units in resources.items())

    def _commit(self, run_id: str, resources: dict[str, int]) -> None:
        for key, units in resources.items():
            self._capacity[key] = self._capacity.get(key, 0) - units
        self._held[run_id] = dict(resources)


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
