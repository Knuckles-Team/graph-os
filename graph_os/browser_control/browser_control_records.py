"""Typed EG ``ControlLease`` records backing browser-control authority state.

Leases, attended-arm receipts, recent-auth receipts and catalog registrations
are all tenant-bound, immutable-grant records with one ``revision`` and a
closed status machine (``active -> consumed | revoked | expired``,
``consumed -> revoked | expired``). The engine owns tenant binding, the
transition table and revision CAS; graph-os only shapes requests through the
process engine's session-routed synchronous client. Times are integer
milliseconds, floor-converted so a record never outlives its authority.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "ControlRecordUnavailable",
    "issue_record",
    "ms",
    "read_record",
    "session_tenant",
    "transition_record",
]


class ControlRecordUnavailable(RuntimeError):
    """The connected engine does not serve the ControlLease authority."""


def _control_leases(engine: Any) -> Any:
    compute = getattr(engine, "graph_compute", None)
    namespace = getattr(getattr(compute, "client", None), "control_leases", None)
    if namespace is None:
        raise ControlRecordUnavailable("EG ControlLease authority is unavailable")
    return namespace


def ms(seconds: float) -> int:
    """Floor seconds to EG's integer milliseconds; never extends a record."""
    return math.floor(seconds * 1000)


def session_tenant() -> str:
    """The verified ambient session's tenant; every record is bound to it."""
    from agent_utilities.api import resolve_session

    return str(resolve_session().tenant)


def issue_record(
    engine: Any,
    *,
    tenant: str,
    record_id: str,
    kind: str,
    grant: dict[str, Any],
    issued_at: float,
    expires_at: float,
    hard_expires_at: float,
    idempotency_key: str,
) -> bool:
    """Issue one record; ``False`` when the id already exists (a collision)."""
    answer = _control_leases(engine).issue(
        tenant=tenant,
        lease_id=record_id,
        kind=kind,
        grant=grant,
        issued_at_ms=ms(issued_at),
        expires_at_ms=ms(expires_at),
        hard_expires_at_ms=ms(hard_expires_at),
        idempotency_key=idempotency_key,
    )
    return answer.get("outcome") == "issued"


def read_record(
    engine: Any, *, tenant: str, record_id: str, kind: str
) -> dict[str, Any] | None:
    """The record's grant with ``status``/``revision``/times, or ``None``.

    A record of another kind reads as absent. Times are returned in seconds.
    """
    view = _control_leases(engine).get(tenant=tenant, lease_id=record_id)
    if not isinstance(view, dict) or view.get("kind") != kind:
        return None
    grant = view.get("grant")
    if not isinstance(grant, dict):
        return None
    return {
        **grant,
        "status": view["status"],
        "revision": view["revision"],
        "issued_at": view["issued_at_ms"] / 1000,
        "expires_at": view["expires_at_ms"] / 1000,
        "hard_expires_at": view["hard_expires_at_ms"] / 1000,
    }


def transition_record(
    engine: Any, *, tenant: str, record_id: str, current: dict[str, Any], to: str
) -> bool:
    """CAS one status transition on the revision read in ``current``."""
    revision = int(current["revision"])
    answer = _control_leases(engine).transition(
        tenant=tenant,
        lease_id=record_id,
        expected_revision=revision,
        to=to,
        idempotency_key=f"{record_id}:{revision}:{to}",
    )
    return answer.get("outcome") == "applied"
