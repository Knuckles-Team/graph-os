"""Strict dry-run admission contracts (GRAPHOS-DATA-MARKET-R004, GDM-04).

``AdmissionIntent`` describes one proposed application/source admission.
``AdmissionPlan`` is the dry-run answer: FR-04 requires the plan to list
dialect/version, credentials scope, catalog/mapping readiness, conformance,
connector overlap, tenant boundaries, cutover behavior, and rollback steps,
with zero source mutation or connector retirement. Persisted one-at-a-time
state, approval, health/parity probes, and retirement (FR-05/FR-06's
stateful half) are follow-on work tracked by GDM-05 in
``specs/data-and-market-projections/tasks.md``; this module computes only
the stateless FR-04 dry run and the FR-06 blocking rule.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from graph_os.control_plane._model import ControlPlaneModel

Identifier = Annotated[str, Field(min_length=1, max_length=256)]


class AdmissionIntent(ControlPlaneModel):
    """One proposed application/source admission, as supplied by an operator."""

    app_name: Identifier
    tenant_id: Identifier
    source_dialect: Identifier
    source_version: Identifier
    credentials_scope: tuple[str, ...] = Field(default=(), max_length=64)
    tenant_scope: tuple[str, ...] = Field(default=(), max_length=64)
    mapping_approved: bool = False
    conformance_checked: bool = False
    rollback_proven: bool = False
    connector_overlap: tuple[str, ...] = Field(default=(), max_length=64)


class AdmissionPlan(ControlPlaneModel):
    """Dry-run answer for one :class:`AdmissionIntent`.

    ``ready`` is true only when every FR-06 blocking condition is absent.
    ``rollback_steps`` is populated unconditionally: a dry run must describe
    the exact recovery action before any attach is ever attempted.
    """

    app_name: Identifier
    tenant_id: Identifier
    ready: bool
    blockers: tuple[str, ...]
    rollback_steps: tuple[str, ...]
    connector_overlap: tuple[str, ...]
    cutover_summary: str


__all__ = ["AdmissionIntent", "AdmissionPlan", "Identifier"]
