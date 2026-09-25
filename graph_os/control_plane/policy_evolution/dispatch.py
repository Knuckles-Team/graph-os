"""The leased trainer seam graph-os hands AU's ``PolicyTrainingPath`` (EH-347).

AU's training path takes an ``ExternalPolicyTrainer`` (``async run(spec) ->
TrainerOutcome``) and never optimises weights itself. graph-os supplies that
trainer as this dispatcher: it admits the attempt (capability ``train``
control, host pressure, inference-SLO reserve), holds one fenced resource
lease for exactly the lifetime of the external job, delivers the job through
the injected trainer transport (the A2A delivery seam), and cancels the job
if the lease is lost. The outcome is the transport's own report; graph-os
does not fabricate a success, and AU commits the ``TrainingRun`` receipt
whatever the outcome.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .admission import PolicyTrainingAdmission
from .models import (
    HostCapacity,
    HostPressure,
    PolicyEvolutionControlError,
    TrainingAdmissionRequest,
    TrainingLease,
)

__all__ = ["HostFacts", "LeasedTrainerDispatcher", "TrainerTransport"]


class TrainerTransport(Protocol):
    """Delivers one digest-bound job to the external trainer (A2A)."""

    async def run(self, spec: Any, lease: TrainingLease) -> Any: ...

    async def cancel(self, spec: Any, lease: TrainingLease) -> Any: ...


class HostFacts(Protocol):
    """Current GPU capacity and pressure of every candidate host."""

    async def snapshot(
        self,
    ) -> tuple[Sequence[HostCapacity], dict[str, HostPressure]]: ...


def _spec_capability(spec: Any) -> str:
    policy = getattr(spec, "policy", None)
    return str(getattr(policy, "capability_id", "") or "")


class LeasedTrainerDispatcher:
    """An ``ExternalPolicyTrainer`` bound to one admitted training request."""

    def __init__(
        self,
        admission: PolicyTrainingAdmission,
        request: TrainingAdmissionRequest,
        transport: TrainerTransport,
        hosts: HostFacts,
        clock_ms: Callable[[], int],
        lease_check_interval_s: float = 5.0,
    ) -> None:
        self._admission = admission
        self._request = request
        self._transport = transport
        self._hosts = hosts
        self._clock_ms = clock_ms
        self._interval = lease_check_interval_s

    async def run(self, spec: Any) -> Any:
        if _spec_capability(spec) != self._request.capability_id:
            raise PolicyEvolutionControlError(
                "TRAINING_SPEC_MISMATCH", "job names another capability"
            )
        capacity, pressure = await self._hosts.snapshot()
        lease = await self._admission.admit(
            self._request, capacity, pressure, self._clock_ms()
        )
        try:
            return await self._run_under_lease(spec, lease)
        finally:
            await self._admission.leases.release(lease, self._clock_ms())

    async def _lease_lost(self, lease: TrainingLease) -> None:
        while await self._admission.leases.is_live(lease, self._clock_ms()):
            await asyncio.sleep(self._interval)

    async def _run_under_lease(self, spec: Any, lease: TrainingLease) -> Any:
        job = asyncio.ensure_future(self._transport.run(spec, lease))
        watcher = asyncio.ensure_future(self._lease_lost(lease))
        try:
            await asyncio.wait({job, watcher}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if job.done():
            return job.result()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        return await self._transport.cancel(spec, lease)
