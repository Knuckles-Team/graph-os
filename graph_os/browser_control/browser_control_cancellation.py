"""Cross-boundary browser call cancellation and reconciliation."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from graph_os.browser_control.browser_control_api import (
    BrowserCallReceipt,
    CancelCallRequest,
    ReconcileCallRequest,
)
from graph_os.browser_control.browser_control_common import CancellationEffect
from graph_os.browser_control.browser_control_server import ControlCancelMessage
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
)

_CANCEL_ACK_SECONDS = 2.0
_FENCE_RECOVERY_SECONDS = 1.0
_MAX_FENCE_RECOVERY_SECONDS = 30.0


class BrowserCancellationMixin(BrowserControlMixinState):
    def _start_call_abort(
        self,
        active: _ActiveCall,
        fence_task: asyncio.Future[Any] | None = None,
    ) -> asyncio.Task[None]:
        task = active.abort_task
        if task is None or task.done():
            task = asyncio.create_task(self._abort_call(active, fence_task))
            active.abort_task = task
        return task

    async def _abort_call(
        self,
        active: _ActiveCall,
        fence_task: asyncio.Future[Any] | None,
    ) -> None:
        if fence_task is not None:
            created = await self._recover_fence_ownership(active, fence_task)
            if not created:
                return
        active.timed_out = True
        async with self._lock:
            current = self._active_calls.setdefault(active.call_id, active)
        try:
            await self._cancel_active(current, "caller_cancelled")
        except asyncio.CancelledError:
            self._schedule_unknown_reaper(current)
            raise
        except Exception:  # noqa: BLE001 - durable reaper owns recovery
            self._schedule_unknown_reaper(current)

    async def _recover_fence_ownership(
        self,
        active: _ActiveCall,
        fence_task: asyncio.Future[Any],
    ) -> bool:
        """Resolve create-then-read ambiguity before touching a durable fence."""

        candidate: asyncio.Future[Any] | None = fence_task
        delay = _FENCE_RECOVERY_SECONDS
        while True:
            try:
                if candidate is not None:
                    created, _row = await candidate
                else:
                    created, _row = await self._submit_fence(active, active.request)
                return bool(created)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retry native authority recovery
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_FENCE_RECOVERY_SECONDS)
                candidate = None

    async def cancel_call(self, request: CancelCallRequest) -> BrowserCallReceipt:
        """Cancel a call idempotently and report only an observed effect state."""

        cached = self._completed.get(request.call_id)
        if cached is not None:
            await self._revalidate_completed_call(request.call_id)
            return cached
        active = self._active_calls.get(request.call_id)
        if active is None:
            raise LookupError("browser control call is not active")
        await self._lease_context(active.request.lease_id, allow_expired=True)
        return await self._cancel_active(active, request.reason)

    async def reconcile_call(self, request: ReconcileCallRequest) -> BrowserCallReceipt:
        """Return the latest durable/in-memory knowledge of an uncertain call."""

        cached = self._completed.get(request.call_id)
        if cached is not None:
            await self._revalidate_completed_call(request.call_id)
            return cached
        active = self._active_calls.get(request.call_id)
        if active is None:
            raise LookupError("browser control call is unknown")
        await self._lease_context(active.request.lease_id, allow_expired=True)
        if active.result_future.done() and not active.result_future.cancelled():
            return await self._finalize_result(active, active.result_future.result())
        return self._receipt(
            active,
            status="unknown" if active.timed_out else "dispatched",
            effect=CancellationEffect.UNKNOWN,
        )

    async def _revalidate_completed_call(self, call_id: str) -> None:
        authority = self._completed_authority.get(call_id)
        if authority is None:
            raise PermissionError("browser call authority is no longer available")
        channel, lease_id = authority
        current, _lease = await self._lease_context(lease_id, allow_expired=True)
        if current is not channel:
            raise PermissionError("browser call authority changed")

    async def _cancel_active(
        self, active: _ActiveCall, reason: str
    ) -> BrowserCallReceipt:
        if active.result_future.done() and not active.result_future.cancelled():
            return await self._finalize_result(active, active.result_future.result())
        async with active.dispatch_lock:
            active.cancellation_requested = True
            dispatched = active.dispatched
        effect = (
            await self._cancellation_effect(active, reason)
            if dispatched
            else CancellationEffect.NONE
        )
        if active.result_future.done() and not active.result_future.cancelled():
            return await self._finalize_result(active, active.result_future.result())
        status: Literal["succeeded", "cancelled", "unknown"] = (
            "succeeded"
            if effect is CancellationEffect.BROWSER_REPORTED_COMMITTED
            else "cancelled"
            if effect is CancellationEffect.NONE
            else "unknown"
        )
        return await self._finalize(
            active,
            status=status,
            effect=effect,
            error_code=None if status == "succeeded" else "browser_call_cancelled",
        )

    async def _cancellation_effect(
        self, active: _ActiveCall, reason: str
    ) -> CancellationEffect:
        if active.claim is None:
            return CancellationEffect.NONE
        try:
            await active.channel.send(
                ControlCancelMessage(call_id=active.call_id, reason=reason[:64])
            )
            return await asyncio.wait_for(
                asyncio.shield(active.cancel_future), timeout=_CANCEL_ACK_SECONDS
            )
        except Exception:  # noqa: BLE001 - cancellation ambiguity is reported
            return CancellationEffect.UNKNOWN


__all__ = ["BrowserCancellationMixin"]
