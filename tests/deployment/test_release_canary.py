"""Focused tests for the bounded, non-serving deployment canary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.deployment import release_canary


def test_entry_points_ready_requires_exact_direct_targets(monkeypatch) -> None:
    entries = [
        SimpleNamespace(
            name=name,
            value=value,
        )
        for name, value in release_canary._ENTRY_POINTS.items()
    ]
    monkeypatch.setattr(
        release_canary.importlib.metadata,
        "entry_points",
        lambda **_: entries,
    )
    assert release_canary._entry_points_ready() is True

    entries[0] = SimpleNamespace(name="graph-os", value="agent_utilities.old:main")
    assert release_canary._entry_points_ready() is False


@pytest.mark.spec("GRAPHOS-A2A-R004")
def test_run_canary_reports_only_aggregate_checks(monkeypatch) -> None:
    entries = [
        SimpleNamespace(name=name, value=value)
        for name, value in release_canary._ENTRY_POINTS.items()
    ]
    monkeypatch.setattr(
        release_canary.importlib.metadata,
        "entry_points",
        lambda **_: entries,
    )
    monkeypatch.setattr(release_canary, "_engine_binary_ready", lambda: True)
    monkeypatch.setattr(release_canary, "_numeric_kernel_ready", lambda: True)
    monkeypatch.setattr(
        release_canary, "_served_fastmcp_matches_declared", lambda: True
    )

    report = release_canary.run_canary()

    assert report == {
        "status": "passed",
        "checks": {
            "entry_points": True,
            "engine_binary": True,
            "numeric_kernel": True,
            "served_fastmcp_matches_declared": True,
        },
        "privacySafe": True,
    }
    assert all(key in {"status", "checks", "privacySafe"} for key in report)


@pytest.mark.spec("GRAPHOS-A2A-R004")
def test_declared_fastmcp_major_reads_graph_os_requirement(monkeypatch) -> None:
    monkeypatch.setattr(
        release_canary.importlib.metadata,
        "requires",
        lambda _name: ["anyio>=4.13.0", "fastmcp>=4.0.0b1", "mcp>=2.0.0"],
    )
    assert release_canary._declared_fastmcp_major() == 4


def test_declared_fastmcp_major_is_none_when_absent(monkeypatch) -> None:
    monkeypatch.setattr(
        release_canary.importlib.metadata,
        "requires",
        lambda _name: ["anyio>=4.13.0"],
    )
    assert release_canary._declared_fastmcp_major() is None


@pytest.mark.spec("GRAPHOS-A2A-R004")
def test_served_fastmcp_matches_declared_true_on_matching_major(
    monkeypatch,
) -> None:
    monkeypatch.setattr(release_canary, "_declared_fastmcp_major", lambda: 4)
    monkeypatch.setattr(
        release_canary.importlib.metadata, "version", lambda _name: "4.0.11"
    )
    assert release_canary._served_fastmcp_matches_declared() is True


@pytest.mark.spec("GRAPHOS-A2A-R004")
def test_served_fastmcp_matches_declared_false_on_major_drift(monkeypatch) -> None:
    monkeypatch.setattr(release_canary, "_declared_fastmcp_major", lambda: 4)
    monkeypatch.setattr(
        release_canary.importlib.metadata, "version", lambda _name: "3.3.1"
    )
    assert release_canary._served_fastmcp_matches_declared() is False


def test_served_fastmcp_matches_declared_false_when_unresolvable(monkeypatch) -> None:
    monkeypatch.setattr(release_canary, "_declared_fastmcp_major", lambda: None)
    monkeypatch.setattr(
        release_canary.importlib.metadata, "version", lambda _name: "4.0.11"
    )
    assert release_canary._served_fastmcp_matches_declared() is False


def test_main_requires_json_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        release_canary.main([])
    assert excinfo.value.code == 2
