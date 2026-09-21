"""Attended browser capability lease lifecycle."""

from __future__ import annotations

from typing import Any, Literal, cast

from graph_os.browser_control.browser_control_api import (
    BrowserCallRequest,
    BrowserLeaseReceipt,
    CancelCallRequest,
    IssueLeaseRequest,
    RenewLeaseRequest,
    RevokeLeaseRequest,
)
from graph_os.browser_control.browser_control_binding import binding_properties
from graph_os.browser_control.browser_control_common import (
    DEFAULT_LEASE_SECONDS,
    MAX_LEASE_SECONDS,
)
from graph_os.browser_control.browser_control_descriptor import BrowserToolDescriptor
from graph_os.browser_control.browser_control_durability import (
    create_lease,
    new_lease_id,
    read_lease,
    transition_lease,
)
from graph_os.browser_control.browser_control_registration import (
    active_registration_matches,
)
from graph_os.browser_control.browser_control_state import (
    BrowserControlMixinState,
    _ChannelState,
)
from graph_os.browser_control.browser_control_validation import require_registered


class BrowserLeaseMixin(BrowserControlMixinState):
    async def issue_lease(self, request: IssueLeaseRequest) -> BrowserLeaseReceipt:
        """Issue a five-minute attended lease, bounded by a fifteen-minute cap."""

        channel = await self._channel_for_document(request.document_ref)
        require_registered(channel)
        tools = self._select_tools(channel, request.tool_ids)
        await self._verify_registration(channel)
        policy_references = await self._authorize_capabilities(channel, tools)
        issued_at = self._clock()
        hard_expires_at = min(
            issued_at + MAX_LEASE_SECONDS,
            channel.refs.attended_arm_expires_at,
        )
        expires_at = min(
            issued_at + min(request.requested_ttl_seconds, DEFAULT_LEASE_SECONDS),
            hard_expires_at,
        )
        lease_id = new_lease_id()
        await self._sync_runner(
            lambda: create_lease(
                self._authority,
                lease_id=lease_id,
                refs=channel.refs,
                catalog_digest=channel.catalog_digest,
                tool_ids=request.tool_ids,
                schema_digests={tool.tool_id: tool.schema_digest for tool in tools},
                policy_references=policy_references,
                issued_at=issued_at,
                expires_at=expires_at,
                hard_expires_at=hard_expires_at,
            )
        )
        return BrowserLeaseReceipt(
            lease_id=lease_id,
            document_ref=request.document_ref,
            tool_ids=request.tool_ids,
            registration_generation=channel.refs.registration_generation,
            expires_at=expires_at,
            hard_expires_at=hard_expires_at,
            status="active",
        )

    async def renew_lease(self, request: RenewLeaseRequest) -> BrowserLeaseReceipt:
        """Refuse renewal without a newly authenticated and armed channel."""

        raise PermissionError(
            "lease renewal requires fresh step-up, channel registration, and issuance"
        )

    async def revoke_lease(self, request: RevokeLeaseRequest) -> BrowserLeaseReceipt:
        """Revoke one lease and converge all of its active calls on cancellation."""

        channel, current = await self._lease_context(
            request.lease_id, allow_expired=True
        )
        status = str(current.get("status") or "")
        if status == "active":
            updated = await self._sync_runner(
                lambda: transition_lease(
                    self._authority,
                    request.lease_id,
                    current=current,
                    updates={"status": "revoked"},
                )
            )
            if not updated:
                raise RuntimeError("browser control lease changed concurrently")
            status = "revoked"
        for active in list(self._active_calls.values()):
            if active.request.lease_id == request.lease_id:
                await self.cancel_call(
                    CancelCallRequest(call_id=active.call_id, reason=request.reason)
                )
        return BrowserLeaseReceipt(
            lease_id=request.lease_id,
            document_ref=channel.binding.document_ref,
            tool_ids=tuple(current["tool_ids"]),
            registration_generation=channel.refs.registration_generation,
            expires_at=float(current["expires_at"]),
            hard_expires_at=float(current["hard_expires_at"]),
            status=cast(Literal["active", "revoked", "expired"], status),
        )

    async def _verify_registration(self, channel: _ChannelState) -> None:
        matches = await self._sync_runner(
            lambda: active_registration_matches(
                self._authority,
                channel.refs,
                channel.catalog_digest,
                channel.tool_scope_digest,
            )
        )
        if not matches:
            raise PermissionError("browser registration is stale")

    def _select_tools(
        self, channel: _ChannelState, tool_ids: tuple[str, ...]
    ) -> tuple[BrowserToolDescriptor, ...]:
        require_registered(channel)
        tools: list[BrowserToolDescriptor] = []
        roles = set(getattr(channel.binding.session.actor, "roles", ()) or ())
        for tool_id in tool_ids:
            descriptor = channel.catalog.get(tool_id)
            if descriptor is None:
                raise PermissionError("lease requested an unregistered tool")
            if not set(descriptor.required_roles).issubset(roles):
                raise PermissionError("browser tool role requirement is not satisfied")
            tools.append(descriptor)
        return tuple(tools)

    async def _lease_context(
        self, lease_id: str, *, allow_expired: bool = False
    ) -> tuple[_ChannelState, dict[str, Any]]:
        current = await self._sync_runner(lambda: read_lease(self._authority, lease_id))
        if current is None:
            raise PermissionError("browser control lease is unavailable")
        authority_expiry = min(
            float(current["attended_arm_expires_at"]),
            float(current["access_token_expires_at"]),
        )
        if current["status"] == "active" and authority_expiry <= self._clock():
            await self._expire_lease(lease_id, current)
            document_ref = str(current["document_reference"])
            async with self._lock:
                channel = self._channels.get(document_ref)
            if channel is not None and not channel.closed:
                await self._disconnect(channel, "lease_authority_expired")
            raise PermissionError("browser control lease authority expired")
        document_ref = str(current["document_reference"])
        channel = await self._channel_for_document(document_ref)
        require_registered(channel)
        if not self._lease_matches(channel, current):
            raise PermissionError("browser control lease binding is stale")
        await self._verify_registration(channel)
        current = await self._validate_lease_status(
            lease_id, current, allow_expired=allow_expired
        )
        return channel, current

    async def _validate_lease_status(
        self,
        lease_id: str,
        current: dict[str, Any],
        *,
        allow_expired: bool,
    ) -> dict[str, Any]:
        status = str(current.get("status") or "")
        if status != "active":
            if allow_expired and status in {"revoked", "expired"}:
                return current
            raise PermissionError("browser control lease is not active")
        if float(current.get("expires_at") or 0.0) > self._clock():
            return current
        await self._expire_lease(lease_id, current)
        if not allow_expired:
            raise PermissionError("browser control lease expired")
        return {**current, "status": "expired"}

    @staticmethod
    def _lease_matches(channel: _ChannelState, lease: dict[str, Any]) -> bool:
        expected = binding_properties(channel.refs)
        try:
            timing_matches = (
                0
                < float(lease["issued_at"])
                <= float(lease["expires_at"])
                <= float(lease["hard_expires_at"])
                <= channel.refs.attended_arm_expires_at
                <= channel.refs.access_token_expires_at
            )
        except (KeyError, TypeError, ValueError):
            return False
        return timing_matches and all(
            lease.get(key) == value for key, value in expected.items()
        )

    async def _expire_lease(self, lease_id: str, current: dict[str, Any]) -> None:
        await self._sync_runner(
            lambda: transition_lease(
                self._authority,
                lease_id,
                current=current,
                updates={"status": "expired"},
            )
        )

    @staticmethod
    def _descriptor_for_call(
        channel: _ChannelState,
        lease: dict[str, Any],
        request: BrowserCallRequest,
    ) -> BrowserToolDescriptor:
        descriptor = channel.catalog.get(request.tool_id)
        allowed = lease.get("tool_ids")
        digests = lease.get("schema_digests")
        if (
            descriptor is None
            or not isinstance(allowed, list)
            or request.tool_id not in allowed
            or not isinstance(digests, dict)
            or digests.get(request.tool_id) != request.schema_digest
            or descriptor.schema_digest != request.schema_digest
        ):
            raise PermissionError("browser call is outside its leased descriptor")
        return descriptor


__all__ = ["BrowserLeaseMixin"]
