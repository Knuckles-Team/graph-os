"""Deterministic browser call admission, confirmation, and dispatch."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

from agent_utilities.knowledge_graph.core.work_durability import (
    claim_specific,
)

from graph_os.browser_control.browser_control_api import (
    BrowserCallReceipt,
    BrowserCallRequest,
)
from graph_os.browser_control.browser_control_client import ControlResultMessage
from graph_os.browser_control.browser_control_common import (
    CancellationEffect,
    MutationClass,
)
from graph_os.browser_control.browser_control_durability import submit_call_fence
from graph_os.browser_control.browser_control_server import ControlCallMessage
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
)
from graph_os.browser_control.browser_control_validation import verify_attended_arm

_CANCEL_ACK_SECONDS = 2.0


class BrowserDispatchMixin(BrowserControlMixinState):
    async def execute_call(self, request: BrowserCallRequest) -> BrowserCallReceipt:
        channel, lease = await self._lease_context(request.lease_id)
        active, cached = await self._prepare_active_call(channel, lease, request)
        if cached is not None:
            return cached
        if active is None:
            raise RuntimeError("browser call preparation returned no authority")
        if not active.authorized:
            await self._audit(active, status="denied", error="policy_denied")
            return self._receipt(
                active,
                status="denied",
                effect=CancellationEffect.NONE,
                error_code="policy_denied",
            )
        fence_task = asyncio.ensure_future(self._submit_fence(active, request))
        try:
            created, row = await asyncio.shield(fence_task)
        except asyncio.CancelledError:
            abort = self._start_call_abort(active, fence_task)
            await asyncio.shield(abort)
            raise
        except Exception:
            self._start_call_abort(active, fence_task)
            raise
        if not created:
            return await self._replayed_call(active, row)
        try:
            await self._audit(active, status="pending")
        except asyncio.CancelledError:
            abort = self._start_call_abort(active)
            await asyncio.shield(abort)
            raise
        except Exception:
            self._start_call_abort(active)
            raise
        try:
            async with self._lock:
                self._active_calls[active.call_id] = active
            return await self._execute_admitted_call(active)
        except asyncio.CancelledError:
            abort = self._start_call_abort(active)
            await asyncio.shield(abort)
            raise

    async def _submit_fence(
        self, active: _ActiveCall, request: BrowserCallRequest
    ) -> tuple[bool, dict[str, Any] | None]:
        return cast(
            tuple[bool, dict[str, Any] | None],
            await self._sync_runner(
                lambda: submit_call_fence(
                    self._engine,
                    refs=active.channel.refs,
                    item_id=active.item_id,
                    call_id=active.call_id,
                    request_digest=active.request_digest,
                    lease_id=request.lease_id,
                    tool_id=request.tool_id,
                    schema_digest=request.schema_digest,
                    policy_reference=active.policy_reference,
                    confirmation_digest=active.confirmation_digest,
                    admission_reference=active.admission_reference,
                )
            ),
        )

    async def _execute_admitted_call(self, active: _ActiveCall) -> BrowserCallReceipt:
        try:
            return await self._dispatch_and_wait(active)
        except TimeoutError:
            active.timed_out = True
            return await self._cancel_active(active, "timeout")
        except Exception:
            if active.claim is None:
                return await self._finalize(
                    active,
                    status="cancelled",
                    effect=CancellationEffect.NONE,
                    error_code="browser_call_not_dispatched",
                )
            return await self._finalize(
                active,
                status="unknown",
                effect=CancellationEffect.UNKNOWN,
                error_code="browser_channel_unavailable",
            )

    async def _dispatch_and_wait(self, active: _ActiveCall) -> BrowserCallReceipt:
        if active.confirmation_digest is not None:
            terminal = await self._confirm(active, active.request.timeout_seconds)
            if terminal is not None:
                return terminal
        _channel, lease = await self._lease_context(active.request.lease_id)
        claim_now = self._clock()
        authority_deadline = min(
            float(lease["expires_at"]),
            float(lease["hard_expires_at"]),
            active.channel.refs.attended_arm_expires_at,
            active.channel.refs.access_token_expires_at,
        )
        remaining = authority_deadline - claim_now
        if remaining < 0.001:
            raise PermissionError("browser call authority expires before dispatch")
        claim = await self._sync_runner(
            lambda: claim_specific(
                self._engine,
                active.item_id,
                token=f"browser-control:{active.request_digest[:32]}",
                now=claim_now,
                lease_ttl_s=min(
                    active.request.timeout_seconds + _CANCEL_ACK_SECONDS,
                    remaining,
                ),
            )
        )
        if claim is None:
            if active.terminal_future.done():
                return active.terminal_future.result()
            return await self._finalize(
                active,
                status="unknown",
                effect=CancellationEffect.UNKNOWN,
                error_code="dispatch_fence_unavailable",
            )
        active.claim = claim
        return await self._dispatch_claimed(active, authority_deadline)

    async def _dispatch_claimed(
        self, active: _ActiveCall, authority_deadline: float
    ) -> BrowserCallReceipt:
        if active.cancellation_requested:
            return await self._finalize(
                active,
                status="cancelled",
                effect=CancellationEffect.NONE,
                error_code="browser_call_cancelled",
            )
        dispatched = await self._dispatch(active, authority_deadline=authority_deadline)
        if not dispatched:
            if active.terminal_future.done():
                return active.terminal_future.result()
            return await self._finalize(
                active,
                status="cancelled",
                effect=CancellationEffect.NONE,
                error_code="browser_call_cancelled",
            )
        terminal = await self._audit_lifecycle(active, status="dispatched")
        if terminal is not None:
            return terminal
        return await self._await_result(active, active.request.timeout_seconds)

    async def _dispatch(
        self, active: _ActiveCall, *, authority_deadline: float
    ) -> bool:
        authorization: Literal["read", "confirmed_mutation"] = (
            "read"
            if active.descriptor.mutation_class is MutationClass.READ
            else "confirmed_mutation"
        )
        await self._revalidate_channel_authority(active.channel)
        await verify_attended_arm(self, active.channel)
        async with active.dispatch_lock:
            async with self._lock:
                document_ref = active.channel.binding.document_ref
                if active.cancellation_requested or active.terminal_future.done():
                    return False
                if (
                    active.channel.closed
                    or self._channels.get(document_ref) is not active.channel
                    or self._clock() >= authority_deadline
                ):
                    raise PermissionError("browser channel changed before dispatch")
                await active.channel.send(
                    ControlCallMessage(
                        call_id=active.call_id,
                        lease_id=active.request.lease_id,
                        tool_id=active.request.tool_id,
                        arguments=active.request.arguments,
                        authorization=authorization,
                        confirmation_digest=active.confirmation_digest,
                    )
                )
                active.dispatched = True
        return True

    async def _await_result(
        self, active: _ActiveCall, timeout: float
    ) -> BrowserCallReceipt:
        waiters: set[asyncio.Future[Any]] = {
            active.result_future,
            active.terminal_future,
        }
        done, _pending = await asyncio.wait(
            waiters,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError
        if active.terminal_future in done:
            return active.terminal_future.result()
        return await self._finalize_result(active, active.result_future.result())

    async def _finalize_result(
        self, active: _ActiveCall, message: ControlResultMessage
    ) -> BrowserCallReceipt:
        if message.status == "succeeded":
            return await self._finalize(
                active,
                status="succeeded",
                effect=CancellationEffect.BROWSER_REPORTED_COMMITTED,
                result=message.result,
            )
        return await self._finalize(
            active,
            status="failed",
            effect=CancellationEffect.NONE,
            error_code=message.error_code or "browser_call_failed",
        )


__all__ = ["BrowserDispatchMixin"]
