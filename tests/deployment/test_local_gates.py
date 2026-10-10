"""Tests for the local-gate push/deploy enforcement primitive."""

from __future__ import annotations

import pytest

from graph_os.deployment.local_gates import (
    GateBlockedError,
    GateReport,
    require_green_local_gates,
    validate_test_namespace,
)


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_push_blocked_while_a_local_gate_is_red() -> None:
    reports = [
        GateReport(name="ruff", green=True),
        GateReport(
            name="kubernetes-test-namespace", green=False, detail="pod not ready"
        ),
    ]
    with pytest.raises(GateBlockedError) as excinfo:
        require_green_local_gates(reports)
    assert excinfo.value.failing == ("kubernetes-test-namespace",)


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_push_proceeds_once_every_local_gate_is_green() -> None:
    reports = [
        GateReport(name="ruff", green=True),
        GateReport(
            name="kubernetes-test-namespace",
            green=True,
            detail="namespace=release-test",
        ),
    ]
    require_green_local_gates(reports)  # does not raise


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_push_blocked_when_no_local_gate_has_reported_yet() -> None:
    with pytest.raises(GateBlockedError):
        require_green_local_gates([])


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_validate_test_namespace_runs_probe_and_reports_green() -> None:
    calls: list[str] = []

    def probe() -> None:
        calls.append("called")

    report = validate_test_namespace("release-test", probe)
    assert report.green is True
    assert calls == ["called"]


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_validate_test_namespace_reports_red_when_probe_raises() -> None:
    def probe() -> None:
        raise RuntimeError("workload not ready")

    report = validate_test_namespace("release-test", probe)
    assert report.green is False
    assert "workload not ready" in report.detail


@pytest.mark.spec("GRAPHOS-RELEASE-R002")
def test_validate_test_namespace_rejects_a_malformed_namespace() -> None:
    report = validate_test_namespace("Not_Valid!", lambda: None)
    assert report.green is False
    assert "invalid namespace" in report.detail
