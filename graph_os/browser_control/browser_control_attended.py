"""Single-use attended-arm receipt authority for browser control.

Two EG ``ControlLease`` records per arm: a single-use recent-auth receipt
(``recent_auth:<grant_ref>``, kind ``browser.recent_auth``) that finalization
consumes exactly once, and the arm itself under ``grant_ref`` (kind
``browser.attended_arm``), which the channel consumes exactly once and which
is later revoked or expired. EG's status machine and revision CAS are the
single-use proof.
"""

from __future__ import annotations

from typing import Any, Literal

from graph_os.browser_control.browser_control_attendance_api import RecentAuthGrant
from graph_os.browser_control.browser_control_binding import (
    BindingReferences,
    binding_properties,
)
from graph_os.browser_control.browser_control_records import (
    issue_record,
    read_record,
    transition_record,
)

_ARM_KIND = "browser.attended_arm"
_RECENT_KIND = "browser.recent_auth"

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


def _recent_id(grant_ref: str) -> str:
    return f"recent_auth:{grant_ref}"


def _read_attended_arm(engine: Any, refs: BindingReferences) -> dict[str, Any] | None:
    return read_record(
        engine,
        tenant=refs.tenant,
        record_id=refs.attended_arm_reference,
        kind=_ARM_KIND,
    )


def _transition_arm(
    engine: Any, refs: BindingReferences, current: dict[str, Any], *, status: str
) -> bool:
    return transition_record(
        engine,
        tenant=refs.tenant,
        record_id=refs.attended_arm_reference,
        current=current,
        to=status,
    )


def consume_attended_arm(engine: Any, refs: BindingReferences, *, now: float) -> None:
    """Consume one pre-existing, live, exact-bound attended arm receipt."""

    current = _read_attended_arm(engine, refs)
    _require_exact_arm(current, refs, message="does not match the channel")
    assert current is not None
    if current.get("status") != "active":
        raise PermissionError("attended arm receipt is not active")
    if float(current.get("attended_arm_expires_at") or 0.0) <= now:
        _transition_arm(engine, refs, current, status="expired")
        raise PermissionError("attended arm receipt expired")
    if not _transition_arm(engine, refs, current, status="consumed"):
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
    engine: Any,
    grant: RecentAuthGrant,
    refs: BindingReferences,
    *,
    now: float,
) -> None:
    """Spend the recent-auth receipt once, then issue the exact bound arm."""

    if not grant.grant_issued_at <= now < grant.grant_expires_at:
        raise PermissionError("recent-auth grant expired")
    recent = _recent_properties(refs, grant)
    receipt = _ensure_recent_auth(engine, refs.tenant, grant, recent)
    spent = transition_record(
        engine,
        tenant=refs.tenant,
        record_id=_recent_id(grant.grant_ref),
        current=receipt,
        to="consumed",
    )
    if not spent:
        raise PermissionError("recent-auth grant was finalized concurrently")
    issued = issue_record(
        engine,
        tenant=refs.tenant,
        record_id=grant.grant_ref,
        kind=_ARM_KIND,
        grant={**recent, **binding_properties(refs), "finalized_at": now},
        issued_at=now,
        expires_at=refs.attended_arm_expires_at,
        hard_expires_at=refs.attended_arm_expires_at,
        idempotency_key=f"arm:{grant.grant_ref}",
    )
    if not issued:
        raise PermissionError("attended arm receipt already exists")


def _ensure_recent_auth(
    engine: Any, tenant: str, grant: RecentAuthGrant, recent: dict[str, Any]
) -> dict[str, Any]:
    record_id = _recent_id(grant.grant_ref)
    issue_record(
        engine,
        tenant=tenant,
        record_id=record_id,
        kind=_RECENT_KIND,
        grant=recent,
        issued_at=grant.grant_issued_at,
        expires_at=grant.grant_expires_at,
        hard_expires_at=grant.grant_expires_at,
        idempotency_key=record_id,
    )
    current = read_record(engine, tenant=tenant, record_id=record_id, kind=_RECENT_KIND)
    if (
        current is None
        or current.get("status") != "active"
        or any(current.get(key) != value for key, value in recent.items())
    ):
        raise PermissionError("recent-auth grant was replayed or changed")
    return current


def attended_arm_matches(engine: Any, refs: BindingReferences, *, now: float) -> bool:
    """Verify this channel still owns its unexpired consumed arm receipt."""

    current = _read_attended_arm(engine, refs)
    if current is None:
        return False
    expected = binding_properties(refs)
    if current.get("status") != "consumed" or any(
        current.get(key) != value for key, value in expected.items()
    ):
        return False
    if float(current.get("attended_arm_expires_at") or 0.0) > now:
        return True
    _transition_arm(engine, refs, current, status="expired")
    return False


def revoke_attended_arm(
    engine: Any, refs: BindingReferences
) -> Literal["revoked", "expired"]:
    """Retire the arm receipt owned by a disconnecting document channel."""

    current = _read_attended_arm(engine, refs)
    _require_exact_arm(current, refs, message="does not match revocation")
    assert current is not None
    status = str(current.get("status") or "")
    if status == "revoked":
        return "revoked"
    if status == "expired":
        return "expired"
    if status not in {"active", "consumed"}:
        raise PermissionError("attended arm receipt cannot be revoked")
    if not _transition_arm(engine, refs, current, status="revoked"):
        _verify_revoked(engine, refs)
    return "revoked"


def _verify_revoked(engine: Any, refs: BindingReferences) -> None:
    latest = _read_attended_arm(engine, refs)
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
