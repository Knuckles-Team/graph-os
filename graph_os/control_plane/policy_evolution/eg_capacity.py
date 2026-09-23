"""Training capacity and leases on EG's ``CapacityCell``/``CapacityLease``.

One cell per training-capable host (``policy-train/gpu/<host>``, resource
class ``gpu``, amounts in MiB). The protected inference set's reservation is
the cell's ``reserved_floor`` and training acquires at
``background_ingestion`` priority — the one priority the engine never lets
spend a floor — so the SLO reserve is enforced by the ledger itself. A
dimension change (host capacity, reserve or policy digest) advances the
cell's epoch by CAS, which stales every lease taken under the old epoch.

The client is the tenant's session-routed async EG client; its
``capacity_leases`` namespace is the only writer.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import (
    HostCapacity,
    PolicyEvolutionControlError,
    TrainingAdmissionRequest,
    TrainingLease,
)

__all__ = ["EgCapacityLeaseBook", "training_cell_id"]

_MIB = 1024 * 1024
_LIVE_STATES = frozenset({"active", "renewed"})
_ACQUIRED = frozenset({"accepted", "replayed"})
_REFUSALS = {
    "exhausted": "TRAINING_SLO_RESERVE",
    "backpressure": "TRAINING_LEDGER_BACKPRESSURE",
    "stale_epoch": "TRAINING_LEDGER_STALE_EPOCH",
    "idempotency_conflict": "TRAINING_ATTEMPT_ALREADY_LEASED",
}
_STATUS_PAGES = 16


def training_cell_id(host_id: str) -> str:
    return f"policy-train/gpu/{host_id}"


def _mib(amount_bytes: int) -> int:
    return -(-amount_bytes // _MIB)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class EgCapacityLeaseBook:
    """A :class:`TrainingLeaseBook` over one tenant's EG capacity ledger."""

    def __init__(
        self,
        client: Any,
        *,
        tenant_ref: str,
        owner_digest: str,
        policy_digest: str,
    ) -> None:
        self._capacity = client.capacity_leases
        self._tenant = tenant_ref
        self._owner = owner_digest
        self._policy = policy_digest

    async def _status(
        self, cell_id: str, lease_id: str | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cells: list[dict[str, Any]] = []
        leases: list[dict[str, Any]] = []
        cursor: str | None = None
        for _page in range(_STATUS_PAGES):
            answer = await self._capacity.status(
                {
                    "schema_version": "1",
                    "tenant_ref": self._tenant,
                    "cell_id": cell_id,
                    "lease_id": lease_id,
                    "max_count": 128,
                    "cursor": cursor,
                }
            )
            cells.extend(answer["cells"])
            leases.extend(answer["leases"])
            cursor = answer["next_cursor"]
            if cursor is None:
                return cells, leases
        raise PolicyEvolutionControlError(
            "TRAINING_LEDGER_UNBOUNDED", "capacity status did not converge"
        )

    async def _cell(self, host_id: str) -> dict[str, Any] | None:
        cells, _leases = await self._status(training_cell_id(host_id))
        return cells[0] if cells else None

    async def provision(
        self, host: HostCapacity, reserved_bytes: int, now_ms: int
    ) -> None:
        capacity = _mib(host.gpu_memory_total_bytes)
        floor = min(_mib(reserved_bytes), capacity)
        current = await self._cell(host.host_id)
        if current is not None and (
            current["capacity"],
            current["reserved_floor"],
            current["policy_digest"],
        ) == (capacity, floor, self._policy):
            return
        epoch = int(current["epoch"]) if current is not None else 0
        answer = await self._capacity.update_cell(
            {
                "schema_version": "1",
                "cell": {
                    "cell_id": training_cell_id(host.host_id),
                    "parent_id": None,
                    "resource_class": "gpu",
                    "capacity": capacity,
                    "reserved_floor": floor,
                    "epoch": epoch + 1,
                    "policy_digest": self._policy,
                    "updated_at_ms": now_ms,
                },
                "expected_epoch": None if current is None else epoch,
                "now_ms": now_ms,
            }
        )
        if answer["decision"] not in _ACQUIRED:
            raise PolicyEvolutionControlError(
                "TRAINING_LEDGER_PROVISION_REFUSED", str(answer["decision"])
            )

    async def free_bytes(self, host_id: str, now_ms: int) -> int:
        cells, leases = await self._status(training_cell_id(host_id))
        if not cells:
            return 0
        cell = cells[0]
        leased = sum(
            int(lease["amount"])
            for lease in leases
            if lease["state"] in _LIVE_STATES and int(lease["expires_at_ms"]) > now_ms
        )
        spare = int(cell["capacity"]) - int(cell["reserved_floor"]) - leased
        return max(spare, 0) * _MIB

    async def acquire(
        self, request: TrainingAdmissionRequest, host_id: str, now_ms: int
    ) -> TrainingLease:
        attempt = _digest(request.tenant_id, request.work_item_id)
        answer = await self._capacity.acquire(
            {
                "schema_version": "1",
                "tenant_ref": self._tenant,
                "work_item_id": request.work_item_id,
                "owner_digest": self._owner,
                "idempotency_key": f"policy-train:{attempt}",
                "priority": "background_ingestion",
                "demands": [
                    {
                        "cell_id": training_cell_id(host_id),
                        "resource_class": "gpu",
                        "amount": _mib(request.requested_gpu_memory_bytes),
                    }
                ],
                "lease_id": f"policy-train:{attempt[:48]}",
                "ttl_ms": request.lease_ttl_ms,
                "now_ms": now_ms,
                "cost_budget_micros": None,
                "token_budget": None,
            }
        )
        decision = str(answer["decision"])
        if decision not in _ACQUIRED or not answer["leases"]:
            raise PolicyEvolutionControlError(
                _REFUSALS.get(decision, "TRAINING_LEASE_REFUSED"), decision
            )
        lease = answer["leases"][0]
        return TrainingLease(
            lease_id=str(lease["lease_id"]),
            tenant_id=request.tenant_id,
            host_id=host_id,
            cell_id=str(lease["cell_id"]),
            work_item_id=request.work_item_id,
            capability_id=request.capability_id,
            gpu_memory_bytes=int(lease["amount"]) * _MIB,
            lease_epoch=int(lease["lease_epoch"]),
            fence_token=int(lease["fence_token"]),
            expires_at_ms=int(lease["expires_at_ms"]),
        )

    async def is_live(self, lease: TrainingLease, now_ms: int) -> bool:
        cells, leases = await self._status(lease.cell_id, lease.lease_id)
        current = next(
            (row for row in leases if row["lease_id"] == lease.lease_id), None
        )
        if current is None or not cells:
            return False
        return (
            current["state"] in _LIVE_STATES
            and int(current["fence_token"]) == lease.fence_token
            and int(current["lease_epoch"]) == int(cells[0]["epoch"])
            and int(current["expires_at_ms"]) > now_ms
        )

    async def release(self, lease: TrainingLease, now_ms: int) -> None:
        await self._capacity.release(
            {
                "schema_version": "1",
                "tenant_ref": self._tenant,
                "owner_digest": self._owner,
                "leases": [
                    {
                        "lease_id": lease.lease_id,
                        "lease_epoch": lease.lease_epoch,
                        "fence_token": lease.fence_token,
                    }
                ],
                "now_ms": now_ms,
                "ttl_ms": None,
                "idempotency_key": f"{lease.lease_id}:release:{lease.fence_token}",
            }
        )
