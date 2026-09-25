"""Resource-leased admission of external policy-training attempts (EH-347).

Training competes with inference for one bounded GPU pool and never gets an
implicit reservation. A host is eligible only when it is inside every
deployment pressure limit (inode, memory, storage, queue age, pod pressure)
and its durable capacity ledger still has the requested GPU memory free
AFTER the protected inference set's reservation. The ledger is EG's
``CapacityCell``/``CapacityLease`` authority (see :mod:`.eg_capacity`): the
inference reservation is the cell's ``reserved_floor``, and training leases
at the one priority that may never spend that floor, so the engine — not
this process — enforces the six-model SLO reserve atomically.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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


def choose_training_host(
    request: TrainingAdmissionRequest,
    hosts: Sequence[HostCapacity],
    pressure: Mapping[str, HostPressure],
    limits: HostLimits,
    free: Mapping[str, int],
) -> str:
    """The eligible host with the most free training capacity, or a refusal.

    ``free`` is each host's capacity left for training after the protected
    inference floor and every live lease, as the durable ledger reports it; a
    host the ledger has no answer for is not eligible. The refusal names the
    reason class of the last candidate examined (pressure versus SLO reserve).
    """
    best: tuple[int, str] | None = None
    refusal = "TRAINING_NO_HOST"
    for host in hosts:
        if request.host_constraint and host.host_id not in request.host_constraint:
            continue
        reason = _pressure_reason(pressure.get(host.host_id), limits)
        if reason is not None:
            refusal = f"TRAINING_HOST_PRESSURE_{reason.upper()}"
            continue
        available = free.get(host.host_id, 0)
        if available < request.requested_gpu_memory_bytes:
            refusal = "TRAINING_SLO_RESERVE"
            continue
        if best is None or available > best[0]:
            best = (available, host.host_id)
    if best is None:
        raise PolicyEvolutionControlError(refusal)
    return best[1]


class TrainingLeaseBook(Protocol):
    """Durable owner of per-host training capacity and its fenced leases."""

    async def provision(
        self, host: HostCapacity, reserved_bytes: int, now_ms: int
    ) -> None: ...

    async def free_bytes(self, host_id: str, now_ms: int) -> int: ...

    async def acquire(
        self, request: TrainingAdmissionRequest, host_id: str, now_ms: int
    ) -> TrainingLease: ...

    async def is_live(self, lease: TrainingLease, now_ms: int) -> bool: ...

    async def release(self, lease: TrainingLease, now_ms: int) -> None: ...


class PolicyTrainingAdmission:
    """Admit one attempt: ``train`` control, ledger sync, host, then lease."""

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

    async def _free_by_host(
        self, hosts: Sequence[HostCapacity], now_ms: int
    ) -> dict[str, int]:
        free: dict[str, int] = {}
        for host in hosts:
            await self._leases.provision(
                host, self._slo.reserved_on(host.host_id), now_ms
            )
            free[host.host_id] = await self._leases.free_bytes(host.host_id, now_ms)
        return free

    async def admit(
        self,
        request: TrainingAdmissionRequest,
        hosts: Sequence[HostCapacity],
        pressure: Mapping[str, HostPressure],
        now_ms: int,
    ) -> TrainingLease:
        if not self._slo.complete():
            raise PolicyEvolutionControlError(
                "TRAINING_SLO_POLICY_INCOMPLETE",
                f"{len(self._slo.protected)} of {self._slo.required_models} "
                "protected models",
            )
        await require_control(
            self._records, request.capability_id, "train", request.granted_scopes
        )
        free = await self._free_by_host(hosts, now_ms)
        host_id = choose_training_host(request, hosts, pressure, self._limits, free)
        return await self._leases.acquire(request, host_id, now_ms)
