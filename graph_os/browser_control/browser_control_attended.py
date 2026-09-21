"""Single-use attended-arm receipt authority for browser control."""

from __future__ import annotations

from typing import Any, Literal

from graph_os.browser_control.browser_control_attendance_api import RecentAuthGrant
from graph_os.browser_control.browser_control_binding import (
    BINDING_PROPERTY_NAMES,
    BindingReferences,
    binding_properties,
)

_RECENT_BINDING_NAMES = (
    "tenant_reference",
    "actor_reference",
    "login_session_reference",
    "principal_reference",
    "browser_session_reference",
    "origin_reference",
    "route_reference",
    "access_token_expires_at",
    "attended_auth_time",
    "attended_acr",
    "attended_issuer",
    "policy_version",
)


def _read_attended_arm(authority: Any, arm_ref: str) -> dict[str, Any] | None:
    names = (*BINDING_PROPERTY_NAMES, "status")
    returns = ", ".join(f"a.{name} AS {name}" for name in names)
    rows = authority.query_cypher(
        f"MATCH (a:AttendedArmReceipt {{id: $id}}) RETURN {returns} LIMIT 2",
        {"id": arm_ref},
    )
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    return rows[0]


def consume_attended_arm(
    authority: Any, refs: BindingReferences, *, now: float
) -> None:
    """Consume one pre-existing, live, exact-bound attended arm receipt."""

    current = _read_attended_arm(authority, refs.attended_arm_reference)
    _require_exact_arm(current, refs, message="does not match the channel")
    assert current is not None
    if current.get("status") != "active":
        raise PermissionError("attended arm receipt is not active")
    if float(current.get("attended_arm_expires_at") or 0.0) <= now:
        _transition_arm(authority, refs, current, status="expired")
        raise PermissionError("attended arm receipt expired")
    if not _transition_arm(
        authority, refs, current, status="consumed", consumed_at=now
    ):
        raise PermissionError("attended arm receipt was consumed concurrently")


def _require_exact_arm(
    current: dict[str, Any] | None,
    refs: BindingReferences,
    *,
    message: str,
) -> None:
    expected = binding_properties(refs)
    if current is None or any(
        current.get(key) != value for key, value in expected.items()
    ):
        raise PermissionError(f"attended arm receipt {message}")


def _transition_arm(
    authority: Any,
    refs: BindingReferences,
    current: dict[str, Any],
    *,
    status: str,
    **updates: Any,
) -> bool:
    conditions = {name: value for name, value in current.items() if name != "id"}
    return bool(
        authority.compare_and_set_node_fields(
            refs.attended_arm_reference,
            conditions,
            {"status": status, **updates},
        )
    )


def _recent_properties(
    refs: BindingReferences, grant: RecentAuthGrant
) -> dict[str, Any]:
    properties = {name: getattr(refs, name) for name in _RECENT_BINDING_NAMES}
    return {
        **properties,
        "attended_arm_reference": grant.grant_ref,
        "grant_issued_at": grant.grant_issued_at,
        "grant_expires_at": grant.grant_expires_at,
    }


def finalize_attended_arm(
    authority: Any,
    grant: RecentAuthGrant,
    refs: BindingReferences,
    *,
    now: float,
) -> None:
    """Atomically consume recent-auth state into one exact active arm node."""

    if not grant.grant_issued_at <= now < grant.grant_expires_at:
        raise PermissionError("recent-auth grant expired")
    recent = _recent_properties(refs, grant)
    initial = {
        "id": grant.grant_ref,
        "node_type": "AttendedArmReceipt",
        **recent,
        "status": "recent_auth_active",
    }
    _ensure_recent_auth(authority, grant.grant_ref, initial, recent)
    conditions = {**recent, "status": "recent_auth_active"}
    updates = {**binding_properties(refs), "status": "active", "finalized_at": now}
    if not authority.compare_and_set_node_fields(grant.grant_ref, conditions, updates):
        raise PermissionError("recent-auth grant was finalized concurrently")


def _ensure_recent_auth(
    authority: Any,
    grant_ref: str,
    initial: dict[str, Any],
    recent: dict[str, Any],
) -> None:
    if authority.create_node_if_absent(grant_ref, properties=initial):
        return
    names = (*recent, "status")
    returns = ", ".join(f"a.{name} AS {name}" for name in names)
    rows = authority.query_cypher(
        f"MATCH (a:AttendedArmReceipt {{id: $id}}) RETURN {returns} LIMIT 2",
        {"id": grant_ref},
    )
    valid = (
        isinstance(rows, list)
        and len(rows) == 1
        and isinstance(rows[0], dict)
        and rows[0].get("status") == "recent_auth_active"
        and all(rows[0].get(key) == value for key, value in recent.items())
    )
    if not valid:
        raise PermissionError("recent-auth grant was replayed or changed")


def attended_arm_matches(
    authority: Any, refs: BindingReferences, *, now: float
) -> bool:
    """Verify this channel still owns its unexpired consumed arm receipt."""

    current = _read_attended_arm(authority, refs.attended_arm_reference)
    if current is None:
        return False
    expected = binding_properties(refs)
    if current.get("status") != "consumed" or any(
        current.get(key) != value for key, value in expected.items()
    ):
        return False
    if float(current.get("attended_arm_expires_at") or 0.0) > now:
        return True
    conditions = {name: value for name, value in current.items() if name != "id"}
    authority.compare_and_set_node_fields(
        refs.attended_arm_reference, conditions, {"status": "expired"}
    )
    return False


def revoke_attended_arm(
    authority: Any, refs: BindingReferences
) -> Literal["revoked", "expired"]:
    """Retire the arm receipt owned by a disconnecting document channel."""

    current = _read_attended_arm(authority, refs.attended_arm_reference)
    _require_exact_arm(current, refs, message="does not match revocation")
    assert current is not None
    status = str(current.get("status") or "")
    if status == "revoked":
        return "revoked"
    if status == "expired":
        return "expired"
    if status not in {"active", "consumed"}:
        raise PermissionError("attended arm receipt cannot be revoked")
    if not _transition_arm(authority, refs, current, status="revoked"):
        _verify_revoked(authority, refs)
    return "revoked"


def _verify_revoked(authority: Any, refs: BindingReferences) -> None:
    latest = _read_attended_arm(authority, refs.attended_arm_reference)
    expected = binding_properties(refs)
    if (
        latest is None
        or latest.get("status") != "revoked"
        or any(latest.get(key) != value for key, value in expected.items())
    ):
        raise RuntimeError("attended arm revocation changed concurrently")


__all__ = [
    "attended_arm_matches",
    "consume_attended_arm",
    "finalize_attended_arm",
    "revoke_attended_arm",
]
