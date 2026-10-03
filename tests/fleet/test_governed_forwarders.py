"""Session bounds and native forwarding contract for the prepared MCP surface."""

from __future__ import annotations

import pytest

from graph_os.fleet.session_loads import LoadCapExceeded, SessionLoads


def test_default_cap_requires_explicit_lru_and_preserves_other_sessions() -> None:
    clock = [0.0]
    sessions = SessionLoads(clock=lambda: clock[0])
    original = [f"tool-{index}" for index in range(64)]
    sessions.load("first", original)
    sessions.load("second", ["private"])
    with pytest.raises(LoadCapExceeded):
        sessions.load("first", ["overflow"])
    assert sessions.loaded("first") == frozenset(original)
    clock[0] = 1.0
    assert sessions.touch("first", "tool-0")
    result = sessions.load("first", ["overflow"], evict="lru")
    assert result["evicted"] == ["tool-1"]
    assert "tool-0" in sessions.loaded("first")
    assert len(sessions.loaded("first")) == 64
    assert sessions.loaded("second") == frozenset({"private"})


def test_hard_cap_and_one_hour_idle_expiry() -> None:
    with pytest.raises(ValueError, match="1..256"):
        SessionLoads(cap=257)
    clock = [0.0]
    sessions = SessionLoads(cap=256, clock=lambda: clock[0])
    sessions.load("first", [f"tool-{index}" for index in range(256)])
    with pytest.raises(LoadCapExceeded):
        sessions.load("first", ["overflow"])
    clock[0] = 3599.0
    assert sessions.active_keys() == ("first",)
    clock[0] = 3600.0
    assert sessions.active_keys() == ()
    assert sessions.loaded("first") == frozenset()
