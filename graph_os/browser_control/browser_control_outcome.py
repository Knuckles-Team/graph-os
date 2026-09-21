"""Browser call outcome reconciliation and provenance."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from agent_utilities.knowledge_graph.core.work_durability import (
    TERMINAL_WORK_ITEM_STATUSES,
    cancel_work_item,
    commit_result,
    get_work_item,
)
from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_api import BrowserCallReceipt
from graph_os.browser_control.browser_control_common import (
    CancellationEffect,
    content_sha256,
)
from graph_os.browser_control.browser_control_durability import commit_call_outcome
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
)

_COMPLETED_CACHE_SIZE = 256
_UNKNOWN_RECONCILE_SECONDS = 30.0
_NO_RESULT = object()
_CallStatus = Literal["succeeded", "failed", "cancelled", "unknown"]


class BrowserOutcomeMixin(BrowserControlMixinState):
    async def _finalize(
        self,
        active: _ActiveCall,
        *,
        status: _CallStatus,
        effect: CancellationEffect,
        result: Any = _NO_RESULT,
        error_code: str | None = None,
    ) -> BrowserCallReceipt:
        async with active.finalize_lock:
            if active.terminal_future.done():
                return active.terminal_future.result()
            try:
                return await self._finalize_locked(
                    active,
                    status=status,
                    effect=effect,
                    result=result,
                    error_code=error_code,
                )
            except asyncio.CancelledError:
                active.timed_out = True
                self._schedule_unknown_reaper(active)
                raise
            except Exception:  # noqa: BLE001 - preserve uncertain durable work
                active.timed_out = True
                self._schedule_unknown_reaper(active)
                return self._receipt(
                    active,
                    status="unknown",
                    effect=CancellationEffect.UNKNOWN,
                    error_code="durable_outcome_unavailable",
                )

    async def _audit_lifecycle(
        self, active: _ActiveCall, *, status: str
    ) -> BrowserCallReceipt | None:
        """Serialize nonterminal audit writes against terminal finalization."""

        async with active.finalize_lock:
            if active.terminal_future.done():
                return active.terminal_future.result()
            await self._audit(active, status=status)
        return None

    async def _finalize_locked(
        self,
        active: _ActiveCall,
        *,
        status: _CallStatus,
        effect: CancellationEffect,
        result: Any,
        error_code: str | None,
    ) -> BrowserCallReceipt:
        has_result = result is not _NO_RESULT
        audited_result = result if has_result else None
        result_digest = f"sha256:{content_sha256(result)}" if has_result else None
        langfuse_status = await self._langfuse_status(
            run_id=active.run_id, tool_name=active.request.tool_id, status=status
        )
        active.langfuse_status = langfuse_status
        await self._audit(
            active,
            status=status,
            result=audited_result,
            result_digest=result_digest,
            error=error_code,
            cancellation_effect=effect,
        )
        status, effect, error_code = await self._terminalize_work_item(
            active,
            status=status,
            effect=effect,
            result_digest=result_digest,
            error_code=error_code,
        )
        if error_code in {
            "work_item_commit_unavailable",
            "work_item_cancel_unavailable",
        }:
            await self._audit(
                active,
                status=status,
                result=audited_result,
                result_digest=result_digest,
                error=error_code,
                cancellation_effect=effect,
            )
        receipt = self._receipt(
            active,
            status=status,
            effect=effect,
            result=audited_result,
            result_digest=result_digest,
            error_code=error_code,
        )
        if status == "unknown":
            active.timed_out = True
            self._schedule_unknown_reaper(active)
            return receipt
        await self._store_terminal(active, receipt)
        return receipt

    async def _terminalize_work_item(
        self,
        active: _ActiveCall,
        *,
        status: _CallStatus,
        effect: CancellationEffect,
        result_digest: str | None,
        error_code: str | None,
    ) -> tuple[_CallStatus, CancellationEffect, str | None]:
        if active.claim is not None and status != "unknown":
            commit_status = await self._commit_claimed_work_item(
                active,
                status=status,
                effect=effect,
                result_digest=result_digest,
                error_code=error_code,
            )
            if commit_status not in {"committed", "noop"}:
                return "unknown", effect, "work_item_commit_unavailable"
        if active.claim is None and status == "cancelled":
            cancelled = await self._sync_runner(
                lambda: cancel_work_item(
                    self._engine,
                    active.item_id,
                    reason=persistence_reference(
                        "browser_cancel",
                        error_code or "browser_call_cancelled",
                        namespace=active.request_digest,
                    ),
                )
            )
            if not cancelled:
                return (
                    "unknown",
                    CancellationEffect.UNKNOWN,
                    "work_item_cancel_unavailable",
                )
        return status, effect, error_code

    async def _commit_claimed_work_item(
        self,
        active: _ActiveCall,
        *,
        status: _CallStatus,
        effect: CancellationEffect,
        result_digest: str | None,
        error_code: str | None,
    ) -> str:
        return str(
            await self._sync_runner(
                lambda: commit_call_outcome(
                    self._engine,
                    item_id=active.item_id,
                    claim=active.claim or {},
                    request_digest=active.request_digest,
                    status=status,
                    effect=effect.value,
                    result_digest=result_digest,
                    error_code=error_code,
                )
            )
        )

    async def _store_terminal(
        self, active: _ActiveCall, receipt: BrowserCallReceipt
    ) -> None:
        task = active.reaper_task
        abort_task = active.abort_task
        current = asyncio.current_task()
        async with self._lock:
            self._completed[active.call_id] = receipt
            self._completed_authority[active.call_id] = (
                active.channel,
                active.request.lease_id,
            )
            self._completed.move_to_end(active.call_id)
            while len(self._completed) > _COMPLETED_CACHE_SIZE:
                expired_call_id, _receipt = self._completed.popitem(last=False)
                self._completed_authority.pop(expired_call_id, None)
            self._active_calls.pop(active.call_id, None)
        if not active.terminal_future.done():
            active.terminal_future.set_result(receipt)
        for pending in (task, abort_task):
            if pending is not None and pending is not current and not pending.done():
                pending.cancel()

    def _schedule_unknown_reaper(self, active: _ActiveCall) -> None:
        if active.reaper_task is None or active.reaper_task.done():
            active.reaper_task = asyncio.create_task(self._reap_unknown_call(active))

    async def _reap_unknown_call(self, active: _ActiveCall) -> None:
        """Bound uncertain in-memory state after its late-result window."""

        await asyncio.sleep(_UNKNOWN_RECONCILE_SECONDS)
        while True:
            try:
                async with active.finalize_lock:
                    if (
                        active.terminal_future.done()
                        or self._active_calls.get(active.call_id) is not active
                        or not active.timed_out
                    ):
                        return
                    active.langfuse_status = await self._langfuse_status(
                        run_id=active.run_id,
                        tool_name=active.request.tool_id,
                        status="unknown_reaped",
                    )
                    await self._audit(
                        active,
                        status="unknown_reaped",
                        error="reconciliation_window_expired",
                        cancellation_effect=CancellationEffect.UNKNOWN,
                    )
                    closed = await self._close_reaped_work_item(active)
                    if not closed:
                        raise RuntimeError(
                            "unknown browser call WorkItem could not be terminalized"
                        )
                    receipt = self._receipt(
                        active,
                        status="unknown",
                        effect=CancellationEffect.UNKNOWN,
                        error_code="reconciliation_window_expired",
                    )
                    await self._store_terminal(active, receipt)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retain until durable audit recovers
                await asyncio.sleep(_UNKNOWN_RECONCILE_SECONDS)

    async def _close_reaped_work_item(self, active: _ActiveCall) -> bool:
        error_ref = persistence_reference(
            "browser_error",
            "reconciliation_window_expired",
            namespace=active.request_digest,
        )
        if active.claim is not None:
            status = await self._sync_runner(
                lambda: commit_result(
                    self._engine,
                    active.item_id,
                    active.claim or {},
                    outcome="failed",
                    error_ref=error_ref,
                    retryable=False,
                )
            )
            return status in {"committed", "noop"}
        cancelled = await self._sync_runner(
            lambda: cancel_work_item(
                self._engine,
                active.item_id,
                reason=error_ref,
            )
        )
        if cancelled:
            return True
        row = await self._sync_runner(
            lambda: get_work_item(self._engine, active.item_id)
        )
        return bool(row and row.get("status") in TERMINAL_WORK_ITEM_STATUSES)


__all__ = ["BrowserOutcomeMixin"]
