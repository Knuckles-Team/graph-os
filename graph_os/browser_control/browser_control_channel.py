"""Authenticated channel and capability-registration lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from graph_os.browser_control.browser_control_api import (
    BrowserCallReceipt,
    BrowserChannelBinding,
    CatalogRegistrationReceipt,
)
from graph_os.browser_control.browser_control_attended import (
    consume_attended_arm,
)
from graph_os.browser_control.browser_control_attended import (
    revoke_attended_arm as _revoke_attended_arm,
)
from graph_os.browser_control.browser_control_binding import binding_references
from graph_os.browser_control.browser_control_client import (
    BrowserClientMessage,
    CatalogRegisterMessage,
    ControlCancelledMessage,
    ControlConfirmMessage,
    ControlResultMessage,
)
from graph_os.browser_control.browser_control_port import BrowserControlConnection
from graph_os.browser_control.browser_control_registration import (
    register_catalog,
    retire_catalog,
)
from graph_os.browser_control.browser_control_server import (
    BrowserSender,
    ConfirmationRequestMessage,
)
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ActiveCall,
    _ChannelState,
    _Connection,
)
from graph_os.browser_control.browser_control_validation import (
    coerce_binding as _coerce_binding,
)
from graph_os.browser_control.browser_control_validation import (
    revalidate_active_message,
    verify_attended_arm,
)
from graph_os.browser_control.browser_control_validation import (
    validate_json_schema as _validate_json_schema,
)
from graph_os.browser_control.browser_control_validation import (
    validate_origin as _validate_origin,
)
from graph_os.browser_control.browser_control_validation import (
    validated_catalog_digests as _validated_catalog_digests,
)

_AUTHORITY_RETRY_SECONDS = 1.0
_MAX_AUTHORITY_RETRY_SECONDS = 30.0


async def _retire_channel_authority(service: Any, state: _ChannelState) -> None:
    """Retry exact durable retirement without blocking the event loop."""

    delay = _AUTHORITY_RETRY_SECONDS
    while True:
        try:
            if state.catalog_digest:
                await service._sync_runner(
                    lambda: retire_catalog(
                        service._authority, state.refs, state.catalog_digest
                    )
                )
            await service._sync_runner(
                lambda: _revoke_attended_arm(service._authority, state.refs)
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retain authority until recovery
            await asyncio.sleep(delay)
            delay = min(delay * 2, _MAX_AUTHORITY_RETRY_SECONDS)


class BrowserChannelMixin(BrowserControlMixinState):
    async def _confirm(
        self, active: _ActiveCall, timeout: float
    ) -> BrowserCallReceipt | None:
        digest = cast(str, active.confirmation_digest)
        await active.channel.send(
            ConfirmationRequestMessage(
                call_id=active.call_id,
                lease_id=active.request.lease_id,
                tool_id=active.request.tool_id,
                arguments=active.request.arguments,
                confirmation_digest=digest,
            )
        )
        confirmation = cast(asyncio.Future[Any], active.confirm_future)
        terminal = cast(asyncio.Future[Any], active.terminal_future)
        done, _pending = await asyncio.wait(
            {confirmation, terminal},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError
        if active.terminal_future in done:
            return active.terminal_future.result()
        return await self._audit_lifecycle(active, status="confirmed")

    async def open_channel(
        self, binding: BrowserChannelBinding, send: BrowserSender
    ) -> BrowserControlConnection:
        """Attach one attended, server-verified document channel."""

        from graph_os.browser_control.browser_control_runtime import (
            bind_browser_control_owner,
        )

        bind_browser_control_owner(self)
        if not callable(send):
            raise TypeError("browser channel sender must be callable")
        binding = _coerce_binding(binding)
        if not binding.attended:
            raise PermissionError("browser control requires attended opt-in")
        if not binding.login_session_ref.startswith("login_"):
            raise PermissionError("browser control requires a verified login session")
        _validate_origin(binding.origin)
        await self._validate_binding_authority(binding)
        refs = binding_references(binding)
        state = _ChannelState(binding=binding, refs=refs, send=send)
        async with self._lock:
            existing = self._channels.get(binding.document_ref)
            if existing is not None and not existing.closed:
                raise RuntimeError("browser document already has an active channel")
            await self._sync_runner(
                lambda: consume_attended_arm(self._authority, refs, now=self._clock())
            )
            self._channels[binding.document_ref] = state
        return _Connection(self, state)

    async def _receive(
        self, state: _ChannelState, message: BrowserClientMessage
    ) -> CatalogRegistrationReceipt | None:
        await self._revalidate_channel_authority(state)
        if isinstance(message, CatalogRegisterMessage):
            return await self._register(state, message)
        elif isinstance(message, ControlConfirmMessage):
            await self._receive_confirmation(state, message)
        elif isinstance(message, ControlResultMessage):
            await self._receive_result(state, message)
        elif isinstance(message, ControlCancelledMessage):
            await self._receive_cancellation(state, message)
        return None

    async def _register(
        self, state: _ChannelState, message: CatalogRegisterMessage
    ) -> CatalogRegistrationReceipt:
        if state.catalog or state.catalog_digest:
            raise RuntimeError("browser channel catalog is already registered")
        await verify_attended_arm(self, state)
        self._validate_registration_binding(state, message)
        catalog_digest, tool_scope_digest = await self._validated_registration(
            state, message
        )
        digest = await self._publish_registration(
            state, message, catalog_digest, tool_scope_digest
        )
        return CatalogRegistrationReceipt(
            route_id=state.binding.route_id,
            registration_generation=state.binding.registration_generation,
            catalog_digest=digest,
            tool_scope_digest=tool_scope_digest,
        )

    @staticmethod
    def _validate_registration_binding(
        state: _ChannelState, message: CatalogRegisterMessage
    ) -> None:
        if (
            message.route_id != state.binding.route_id
            or message.registration_generation != state.binding.registration_generation
        ):
            raise PermissionError("catalog does not match its channel binding")

    async def _validated_registration(
        self, state: _ChannelState, message: CatalogRegisterMessage
    ) -> tuple[str, str]:
        catalog_digest, tool_scope_digest = await self._sync_runner(
            lambda: _validated_catalog_digests(message.tools)
        )
        if (
            message.catalog_digest != catalog_digest
            or message.tool_scope_digest != tool_scope_digest
            or state.binding.catalog_digest != catalog_digest
            or state.binding.tool_scope_digest != tool_scope_digest
        ):
            raise PermissionError("catalog digest claim does not match its descriptors")
        return catalog_digest, tool_scope_digest

    async def _publish_registration(
        self,
        state: _ChannelState,
        message: CatalogRegisterMessage,
        catalog_digest: str,
        tool_scope_digest: str,
    ) -> str:
        async with self._lock:
            current = self._channels.get(state.binding.document_ref)
            if current is not state or state.closed:
                raise PermissionError("browser channel changed during registration")
            state.catalog_digest = catalog_digest
            state.tool_scope_digest = tool_scope_digest
            registration_task = asyncio.ensure_future(
                self._sync_runner(
                    lambda: register_catalog(self._authority, state.refs, message.tools)
                )
            )
            try:
                digest = cast(str, await asyncio.shield(registration_task))
            except asyncio.CancelledError:
                await registration_task
                await self._sync_runner(
                    lambda: retire_catalog(self._authority, state.refs, catalog_digest)
                )
                raise
            state.catalog = {tool.tool_id: tool for tool in message.tools}
            state.catalog_digest = digest
        return digest

    async def _receive_confirmation(
        self, state: _ChannelState, message: ControlConfirmMessage
    ) -> None:
        active = await revalidate_active_message(self, state, message.call_id)
        if active.confirmation_digest != message.confirmation_digest:
            raise PermissionError(
                "confirmation digest does not match the exact request"
            )
        if not active.confirm_future.done():
            active.confirm_future.set_result(None)

    async def _receive_result(
        self, state: _ChannelState, message: ControlResultMessage
    ) -> None:
        active = await revalidate_active_message(self, state, message.call_id)
        if message.status == "succeeded":
            await self._sync_runner(
                lambda: _validate_json_schema(
                    active.descriptor.output_schema, message.result
                )
            )
        if not active.result_future.done():
            active.result_future.set_result(message)
        if active.timed_out:
            await self._finalize_result(active, message)

    async def _receive_cancellation(
        self, state: _ChannelState, message: ControlCancelledMessage
    ) -> None:
        active = await revalidate_active_message(self, state, message.call_id)
        if not active.cancel_future.done():
            active.cancel_future.set_result(message.effect)

    async def _disconnect(self, state: _ChannelState, reason: str) -> None:
        async with self._lock:
            state.closed = True
            if self._channels.get(state.binding.document_ref) is state:
                del self._channels[state.binding.document_ref]
        try:
            for active in list(self._active_calls.values()):
                if active.channel is state:
                    await self._cancel_active(active, "browser_channel_disconnected")
        finally:
            task = state.cleanup_task
            if task is None or task.done():
                task = asyncio.create_task(_retire_channel_authority(self, state))
                state.cleanup_task = task
            await asyncio.shield(task)

    async def _channel_for_document(self, document_ref: str) -> _ChannelState:
        async with self._lock:
            channel = self._channels.get(document_ref)
        if channel is None or channel.closed:
            raise PermissionError("browser control document channel is unavailable")
        if not channel.binding.attended:
            raise PermissionError("browser control channel is unattended")
        await self._revalidate_channel_authority(channel)
        await verify_attended_arm(self, channel)
        async with self._lock:
            if self._channels.get(document_ref) is not channel or channel.closed:
                raise PermissionError(
                    "browser document channel changed during validation"
                )
        return channel

    async def _revalidate_channel_authority(self, channel: _ChannelState) -> None:
        self._validate_ambient_binding(channel.binding)
        try:
            await self._validate_binding_authority(channel.binding)
        except (PermissionError, RuntimeError, TypeError, ValueError):
            await self._disconnect(channel, "authority_revalidation_failed")
            raise


__all__ = ["BrowserChannelMixin"]
