"""Tests for the admission dry-run planner (GRAPHOS-DATA-MARKET-R004, GDM-04).

AD-01 (unit): dry run computes requirements and rollback steps without
mutation or connector retirement. AD-03 (negative): an unapproved mapping, a
broad grant, missing conformance, or missing rollback proof blocks approval.
"""

from __future__ import annotations

from graph_os.control_plane.admission.models import AdmissionIntent
from graph_os.control_plane.admission.service import plan_admission


def _ready_intent(**overrides: object) -> AdmissionIntent:
    base: dict[str, object] = {
        "app_name": "sample_app",
        "tenant_id": "tenant-a",
        "source_dialect": "postgresql",
        "source_version": "16",
        "credentials_scope": ("tenant-a",),
        "tenant_scope": ("tenant-a",),
        "mapping_approved": True,
        "conformance_checked": True,
        "rollback_proven": True,
        "connector_overlap": ("legacy-etl",),
    }
    base.update(overrides)
    return AdmissionIntent.model_validate(base)


def test_dry_run_is_ready_when_every_condition_is_met() -> None:
    plan = plan_admission(_ready_intent())

    assert plan.ready is True
    assert plan.blockers == ()
    assert plan.connector_overlap == ("legacy-etl",)
    assert len(plan.rollback_steps) >= 1


def test_dry_run_always_states_rollback_steps() -> None:
    """AD-01: the plan lists rollback steps whether or not it is ready."""

    ready = plan_admission(_ready_intent())
    blocked = plan_admission(_ready_intent(mapping_approved=False))

    assert ready.rollback_steps
    assert blocked.rollback_steps
    assert ready.rollback_steps == blocked.rollback_steps


def test_unapproved_mapping_blocks_admission() -> None:
    plan = plan_admission(_ready_intent(mapping_approved=False))

    assert plan.ready is False
    assert any("mapping is unapproved" in blocker for blocker in plan.blockers)


def test_missing_conformance_blocks_admission() -> None:
    plan = plan_admission(_ready_intent(conformance_checked=False))

    assert plan.ready is False
    assert any("conformance is missing" in blocker for blocker in plan.blockers)


def test_unproven_rollback_blocks_admission() -> None:
    plan = plan_admission(_ready_intent(rollback_proven=False))

    assert plan.ready is False
    assert any("rollback is unproven" in blocker for blocker in plan.blockers)


def test_broad_grant_blocks_admission() -> None:
    plan = plan_admission(
        _ready_intent(
            credentials_scope=("tenant-a", "tenant-b"), tenant_scope=("tenant-a",)
        )
    )

    assert plan.ready is False
    assert any("broader than the tenant" in blocker for blocker in plan.blockers)
    assert any("tenant-b" in blocker for blocker in plan.blockers)


def test_multiple_blockers_are_all_reported() -> None:
    plan = plan_admission(
        _ready_intent(mapping_approved=False, conformance_checked=False)
    )

    assert plan.ready is False
    assert len(plan.blockers) == 2
