"""Closed validation helpers shared by browser-control lifecycle modules."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from graph_os.browser_control.browser_control_api import BrowserChannelBinding
from graph_os.browser_control.browser_control_attendance_api import RecentAuthGrant


def validate_origin(origin: str) -> None:
    parsed = urlsplit(origin)
    secure_scheme = parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        not secure_scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("browser control origin must be secure and canonical")


def coerce_binding(binding: BrowserChannelBinding) -> BrowserChannelBinding:
    """Validate a structurally compatible binding supplied by agent-webui."""

    if isinstance(binding, BrowserChannelBinding):
        return binding
    try:
        return BrowserChannelBinding(
            session=binding.session,
            login_session_ref=binding.login_session_ref,
            browser_session_ref=binding.browser_session_ref,
            principal_ref=binding.principal_ref,
            origin=binding.origin,
            document_ref=binding.document_ref,
            route_id=binding.route_id,
            registration_generation=binding.registration_generation,
            attended_arm_ref=binding.attended_arm_ref,
            attended_arm_expires_at=binding.attended_arm_expires_at,
            access_token_expires_at=binding.access_token_expires_at,
            catalog_digest=binding.catalog_digest,
            tool_scope_digest=binding.tool_scope_digest,
            attended_arm_issued_at=binding.attended_arm_issued_at,
            attended_auth_time=binding.attended_auth_time,
            attended_acr=binding.attended_acr,
            attended_issuer=binding.attended_issuer,
            session_revalidator=binding.session_revalidator,
            attended=binding.attended,
        )
    except AttributeError as exc:
        raise TypeError("browser channel binding is incomplete") from exc


def coerce_recent_auth_grant(grant: RecentAuthGrant) -> RecentAuthGrant:
    """Validate a structurally compatible server-only recent-auth grant."""

    if isinstance(grant, RecentAuthGrant):
        return grant
    try:
        return RecentAuthGrant(
            session=grant.session,
            grant_ref=grant.grant_ref,
            login_session_ref=grant.login_session_ref,
            browser_session_ref=grant.browser_session_ref,
            principal_ref=grant.principal_ref,
            origin=grant.origin,
            route_id=grant.route_id,
            access_token_expires_at=grant.access_token_expires_at,
            grant_issued_at=grant.grant_issued_at,
            grant_expires_at=grant.grant_expires_at,
            attended_auth_time=grant.attended_auth_time,
            attended_acr=grant.attended_acr,
            attended_issuer=grant.attended_issuer,
        )
    except AttributeError as exc:
        raise TypeError("recent-auth grant is incomplete") from exc


def validate_json_schema(schema: dict[str, Any], value: Any) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=str)
    if errors:
        raise ValueError("browser control payload does not match its registered schema")


def require_registered(state: Any) -> None:
    """Fail closed until one exact catalog has been durably registered."""

    if not state.catalog or not state.catalog_digest or not state.tool_scope_digest:
        raise PermissionError("browser capability catalog is not registered")


async def revalidate_active_message(service: Any, state: Any, call_id: str) -> Any:
    """Recheck the live durable lease and registration for a browser event."""

    active = service._bound_active_call(state, call_id)
    channel, _lease = await service._lease_context(active.request.lease_id)
    if channel is not state:
        raise PermissionError("browser message channel binding changed")
    return active


async def verify_attended_arm(service: Any, state: Any) -> None:
    """Fail closed unless the channel still owns its exact live arm receipt."""

    from graph_os.browser_control.browser_control_attended import attended_arm_matches

    matches = await service._sync_runner(
        lambda: attended_arm_matches(
            service._authority, state.refs, now=service._clock()
        )
    )
    if not matches:
        await service._disconnect(state, "attended_arm_invalid")
        raise PermissionError("attended arm receipt is stale or expired")


def validated_catalog_digests(tools: tuple[Any, ...]) -> tuple[str, str]:
    """Validate each JSON Schema and recompute both authoritative digests."""

    from graph_os.browser_control.browser_control_binding import (
        descriptor_catalog_digest,
        descriptor_tool_scope_digest,
    )

    for tool in tools:
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
    return descriptor_catalog_digest(tools), descriptor_tool_scope_digest(tools)


__all__ = [
    "coerce_binding",
    "coerce_recent_auth_grant",
    "revalidate_active_message",
    "require_registered",
    "verify_attended_arm",
    "validate_json_schema",
    "validate_origin",
    "validated_catalog_digests",
]
