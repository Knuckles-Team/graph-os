"""Dry-run admission planner (GRAPHOS-DATA-MARKET-R004, GDM-04, AD-01/AD-03).

A pure computation over an already-supplied :class:`AdmissionIntent`: it
reads no source, calls no connector, and performs no attach, retirement, or
persisted-state transition. ``plan_admission`` is safe to call as many times
as needed for the same intent (AD-01); it causes no mutation regardless of
the outcome, and FR-06's blocking conditions prevent a caller from treating a
blocked plan as readiness (AD-03).
"""

from __future__ import annotations

from graph_os.control_plane.admission.models import AdmissionIntent, AdmissionPlan

__all__ = ["plan_admission"]


def _blockers(intent: AdmissionIntent) -> tuple[str, ...]:
    """FR-06: the exact conditions that block admission, in a fixed order."""

    found: list[str] = []
    tenant_scope = set(intent.tenant_scope)
    broad_grants = sorted(set(intent.credentials_scope) - tenant_scope)
    if broad_grants:
        found.append(
            "source grants are broader than the tenant: " + ", ".join(broad_grants)
        )
    if not intent.mapping_approved:
        found.append("mapping is unapproved")
    if not intent.conformance_checked:
        found.append("conformance is missing")
    if not intent.rollback_proven:
        found.append("rollback is unproven")
    return tuple(found)


def _rollback_steps(intent: AdmissionIntent) -> tuple[str, ...]:
    """FR-04: the dry run always states recovery action, ready or not."""

    return (
        f"keep '{intent.app_name}' reading through its existing connector(s)",
        f"do not retire any connector overlapping '{intent.app_name}' "
        "until the admitted source is healthy and parity checks pass",
        f"on any attach/verify/activate failure, restore the prior read path "
        f"for tenant '{intent.tenant_id}' and report the exact failed step",
    )


def plan_admission(intent: AdmissionIntent) -> AdmissionPlan:
    """Compute one dry-run :class:`AdmissionPlan`. Causes zero mutation."""

    blockers = _blockers(intent)
    ready = not blockers
    cutover_summary = (
        f"'{intent.app_name}' ({intent.source_dialect} {intent.source_version}) "
        f"would cut over for tenant '{intent.tenant_id}' with no migration of "
        "application-owned tables implied by admission"
        if ready
        else f"'{intent.app_name}' cannot be admitted until every blocker is resolved"
    )
    return AdmissionPlan(
        app_name=intent.app_name,
        tenant_id=intent.tenant_id,
        ready=ready,
        blockers=blockers,
        rollback_steps=_rollback_steps(intent),
        connector_overlap=intent.connector_overlap,
        cutover_summary=cutover_summary,
    )
