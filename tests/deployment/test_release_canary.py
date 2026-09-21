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
    monkeypatch.setattr(release_canary, "_langfuse_fleet_ready", lambda: True)

    report = release_canary.run_canary()

    assert report == {
        "status": "passed",
        "checks": {
            "entry_points": True,
            "engine_binary": True,
            "numeric_kernel": True,
            "langfuse_fleet": True,
        },
        "privacySafe": True,
    }
    assert all(key in {"status", "checks", "privacySafe"} for key in report)


def test_main_requires_json_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        release_canary.main([])
    assert excinfo.value.code == 2
