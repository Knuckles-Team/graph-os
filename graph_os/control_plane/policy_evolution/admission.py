"""Resource-leased admission of external policy-training attempts (EH-347).

Training competes with inference for one bounded GPU pool and never gets an
implicit reservation. A host is eligible only when it is inside every
deployment pressure limit (inode, memory, storage, queue age, pod pressure)
and still has the requested GPU memory free AFTER the protected inference
set's reservations and every live training lease. One fenced lease owns one
attempt (one native WorkItem); a second attempt for the same WorkItem is
refused while the first lease lives.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

from .models import (
    HostCapacity,
    HostLimits,
    HostPressure,
    InferenceSloPolicy,
    PolicyEvolutionControlError,
    TrainingAdmissionRequest,
    TrainingLease,
)
from .records import PolicyRecordReader, require_control

__all__ = [
    "InMemoryTrainingLeaseBook",
    "PolicyTrainingAdmission",
    "TrainingLeaseBook",
    "choose_training_host",
]

_PRESSURE_CHECKS: tuple[tuple[str, Callable[[HostPressure, HostLimits], bool]], ...] = (
    ("inode", lambda p, lim: p.inode_used_ppm > lim.max_inode_used_ppm),
    (
        "memory",
        lambda p, lim: p.memory_available_bytes < lim.min_memory_available_bytes,
    ),
    ("storage", lambda p, lim: p.storage_free_bytes < lim.min_storage_free_bytes),
    ("queue_age", lambda p, lim: p.queue_age_ms > lim.max_queue_age_ms),
    ("pod_pressure", lambda p, _lim: p.pod_pressure),
)


def _pressure_reason(pressure: HostPressure | None, limits: HostLimits) -> str | None:
    if pressure is None:
        return "unobserved"
    for name, exceeded in _PRESSURE_CHECKS:
        if exceeded(pressure, limits):
            return name
    return None


def _free_bytes(
    host: HostCapacity, slo: InferenceSloPolicy, leased: Callable[[str], int]
) -> int:
    return (
        host.gpu_memory_total_bytes
        - slo.reserved_on(host.host_id)
        - leased(host.host_id)
    )


def choose_training_host(
    request: TrainingAdmissionRequest,
    hosts: Sequence[HostCapacity],
    pressure: dict[str, HostPressure],
    limits: HostLimits,
    slo: InferenceSloPolicy,
    leased: Callable[[str], int],
) -> str:
    """The eligible host with the most free GPU memory, or a typed refusal.

    The refusal names the reason of the last candidate examined per class, so
    an operator sees why nothing fit (pressure versus SLO reserve).
    """
    if not slo.complete():
        raise PolicyEvolutionControlError(
            "TRAINING_SLO_POLICY_INCOMPLETE",
            f"{len(slo.protected)} of {slo.required_models} protected models",
        )
    best: tuple[int, str] | None = None
    refusal = "TRAINING_NO_HOST"
    for host in hosts:
        if request.host_constraint and host.host_id not in request.host_constraint:
            continue
        reason = _pressure_reason(pressure.get(host.host_id), limits)
        if reason is not None:
            refusal = f"TRAINING_HOST_PRESSURE_{reason.upper()}"
            continue
        free = _free_bytes(host, slo, leased)
        if free < request.requested_gpu_memory_bytes:
            refusal = "TRAINING_SLO_RESERVE"
            continue
        if best is None or free > best[0]:
            best = (free, host.host_id)
    if best is None:
        raise PolicyEvolutionControlError(refusal)
    return best[1]


class TrainingLeaseBook(Protocol):
    """Durable owner of the fenced training leases."""

    def leased_bytes(self, host_id: str, now_ms: int) -> int: ...

    def acquire(
        self, request: TrainingAdmissionRequest, host_id: str, now_ms: int
    ) -> TrainingLease: ...

    def is_live(self, lease: TrainingLease, now_ms: int) -> bool: ...

    def release(self, lease: TrainingLease) -> None: ...


class InMemoryTrainingLeaseBook:
    """Reference lease book: one live lease per WorkItem, monotonic fences."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, TrainingLease] = {}
        self._fence = 0

    def _live(self, now_ms: int) -> list[TrainingLease]:
        return [
            lease for lease in self._leases.values() if lease.expires_at_ms > now_ms
        ]

    def leased_bytes(self, host_id: str, now_ms: int) -> int:
        with self._lock:
            return sum(
                lease.gpu_memory_bytes
                for lease in self._live(now_ms)
                if lease.host_id == host_id
            )

    def acquire(
        self, request: TrainingAdmissionRequest, host_id: str, now_ms: int
    ) -> TrainingLease:
        with self._lock:
            held = self._leases.get(request.work_item_id)
            if held is not None and held.expires_at_ms > now_ms:
                raise PolicyEvolutionControlError(
                    "TRAINING_ATTEMPT_ALREADY_LEASED", request.work_item_id
                )
            self._fence += 1
            lease = TrainingLease(
                lease_id="training-lease:"
                + hashlib.sha256(
                    f"{request.work_item_id}\x1f{self._fence}".encode()
                ).hexdigest()[:32],
                tenant_id=request.tenant_id,
                host_id=host_id,
                work_item_id=request.work_item_id,
                capability_id=request.capability_id,
                gpu_memory_bytes=request.requested_gpu_memory_bytes,
                fence=self._fence,
                expires_at_ms=now_ms + request.lease_ttl_ms,
            )
            self._leases[request.work_item_id] = lease
            return lease

    def is_live(self, lease: TrainingLease, now_ms: int) -> bool:
        with self._lock:
            return self._leases.get(lease.work_item_id) == lease and (
                lease.expires_at_ms > now_ms
            )

    def revoke(self, work_item_id: str) -> None:
        with self._lock:
            self._leases.pop(work_item_id, None)

    def release(self, lease: TrainingLease) -> None:
        with self._lock:
            if self._leases.get(lease.work_item_id) == lease:
                del self._leases[lease.work_item_id]


class PolicyTrainingAdmission:
    """Admit one attempt: capability ``train`` control, host, then lease."""

    def __init__(
        self,
        records: PolicyRecordReader,
        leases: TrainingLeaseBook,
        slo: InferenceSloPolicy,
        limits: HostLimits,
    ) -> None:
        self._records = records
        self._leases = leases
        self._slo = slo
        self._limits = limits

    @property
    def leases(self) -> TrainingLeaseBook:
        return self._leases

    async def admit(
        self,
        request: TrainingAdmissionRequest,
        hosts: Sequence[HostCapacity],
        pressure: dict[str, HostPressure],
        now_ms: int,
    ) -> TrainingLease:
        await require_control(
            self._records, request.capability_id, "train", request.granted_scopes
        )
        host_id = choose_training_host(
            request,
            hosts,
            pressure,
            self._limits,
            self._slo,
            lambda host: self._leases.leased_bytes(host, now_ms),
        )
        return self._leases.acquire(request, host_id, now_ms)
