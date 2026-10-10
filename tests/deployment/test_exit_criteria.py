"""Tests for the exit-criteria matrix audit mechanism."""

from __future__ import annotations

import pytest

from graph_os.deployment.exit_criteria import ExitCriterion, audit_exit_criteria


@pytest.mark.spec("GRAPHOS-RELEASE-R003.1")
def test_audit_flags_a_row_satisfied_when_its_test_is_collected() -> None:
    matrix = [
        ExitCriterion(
            obligation_id="write-back-receipt",
            description="one write-back receipt",
            test_node_id="tests/test_write_back.py::test_receipt_recorded",
        ),
    ]
    audit = audit_exit_criteria(
        matrix, collected_test_ids=["tests/test_write_back.py::test_receipt_recorded"]
    )
    assert audit.satisfied == ("write-back-receipt",)
    assert audit.missing == ()
    assert audit.all_satisfied is True


@pytest.mark.spec("GRAPHOS-RELEASE-R003.1")
def test_audit_flags_a_row_missing_when_its_test_is_not_collected() -> None:
    matrix = [
        ExitCriterion(
            obligation_id="typed-abstention",
            description="typed-abstention response to an agent-capability question",
            test_node_id="tests/test_abstention.py::test_typed_abstention",
        ),
    ]
    audit = audit_exit_criteria(matrix, collected_test_ids=[])
    assert audit.satisfied == ()
    assert audit.missing == ("typed-abstention",)
    assert audit.all_satisfied is False


@pytest.mark.spec("GRAPHOS-RELEASE-R003.1")
def test_audit_never_marks_an_unregistered_obligation_as_a_silent_pass() -> None:
    audit = audit_exit_criteria(matrix=[], collected_test_ids=["anything::at_all"])
    assert audit.satisfied == ()
    assert audit.missing == ()
    assert (
        audit.all_satisfied is True
    )  # vacuously true over zero rows, never fabricated
