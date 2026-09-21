"""Server-facing finalization and revocation of attended authority."""

from __future__ import annotations

from graph_os.browser_control.browser_control_api import BrowserChannelBinding
from graph_os.browser_control.browser_control_attendance_api import (
    AttendedArmReceipt,
    RecentAuthGrant,
)
from graph_os.browser_control.browser_control_attended import (
    finalize_attended_arm as _finalize_attended_arm,
)
from graph_os.browser_control.browser_control_attended import (
    revoke_attended_arm as _revoke_attended_arm,
)
from graph_os.browser_control.browser_control_binding import binding_references
from graph_os.browser_control.browser_control_state import BrowserControlMixinState
from graph_os.browser_control.browser_control_validation import (
    coerce_binding,
    coerce_recent_auth_grant,
    validate_origin,
)


class BrowserAttendanceMixin(BrowserControlMixinState):
    async def finalize_attended_arm(
        self,
        grant: RecentAuthGrant,
        binding: BrowserChannelBinding,
    ) -> AttendedArmReceipt:
        """Finalize post-redirect current scope into a one-use arm receipt."""

        from graph_os.browser_control.browser_control_runtime import (
            bind_browser_control_owner,
        )

        bind_browser_control_owner(self)
        grant = coerce_recent_auth_grant(grant)
        binding = coerce_binding(binding)
        validate_origin(binding.origin)
        if not self._grant_matches_binding(grant, binding):
            raise PermissionError("recent-auth grant does not match final binding")
        await self._validate_binding_authority(binding)
        now = self._clock()
        if not grant.grant_issued_at <= binding.attended_arm_issued_at <= now:
            raise PermissionError("attended arm issue time is outside the grant")
        refs = binding_references(binding)
        await self._sync_runner(
            lambda: _finalize_attended_arm(self._authority, grant, refs, now=now)
        )
        return AttendedArmReceipt(
            attended_arm_ref=refs.attended_arm_reference,
            attended_arm_expires_at=refs.attended_arm_expires_at,
            catalog_digest=refs.catalog_digest,
            tool_scope_digest=refs.tool_scope_digest,
        )

    async def revoke_attended_arm(
        self, binding: BrowserChannelBinding
    ) -> AttendedArmReceipt:
        """Idempotently revoke one exact finalized or connected arm receipt."""

        from graph_os.browser_control.browser_control_runtime import (
            bind_browser_control_owner,
        )

        bind_browser_control_owner(self)
        binding = coerce_binding(binding)
        validate_origin(binding.origin)
        self._validate_ambient_binding(binding)
        refs = binding_references(binding)
        async with self._lock:
            state = self._channels.get(binding.document_ref)
        if state is not None and not state.closed:
            if state.refs != refs:
                raise PermissionError("attended channel changed before revocation")
            await self._disconnect(state, "attended_arm_revoked")
        status = await self._sync_runner(
            lambda: _revoke_attended_arm(self._authority, refs)
        )
        return AttendedArmReceipt(
            attended_arm_ref=refs.attended_arm_reference,
            attended_arm_expires_at=refs.attended_arm_expires_at,
            catalog_digest=refs.catalog_digest,
            tool_scope_digest=refs.tool_scope_digest,
            status=status,
        )

    @staticmethod
    def _grant_matches_binding(
        grant: RecentAuthGrant, binding: BrowserChannelBinding
    ) -> bool:
        grant_actor = getattr(grant.session.actor, "actor_id", None)
        binding_actor = getattr(binding.session.actor, "actor_id", None)
        return all(
            (
                grant.grant_ref == binding.attended_arm_ref,
                grant.session.tenant == binding.session.tenant,
                grant_actor == binding_actor,
                grant.session.policy_version == binding.session.policy_version,
                grant.login_session_ref == binding.login_session_ref,
                grant.browser_session_ref == binding.browser_session_ref,
                grant.principal_ref == binding.principal_ref,
                grant.origin == binding.origin,
                grant.route_id == binding.route_id,
                grant.access_token_expires_at == binding.access_token_expires_at,
                grant.attended_auth_time == binding.attended_auth_time,
                grant.attended_acr == binding.attended_acr,
                grant.attended_issuer == binding.attended_issuer,
            )
        )


__all__ = ["BrowserAttendanceMixin"]
