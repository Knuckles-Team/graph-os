"""Doctor coverage for the A2A facade's hosted control-plane dependency."""

from __future__ import annotations

import agent_utilities.api as au_api

from graph_os.deployment import doctor


def test_a2a_doctor_fails_until_au_publishes_the_hosted_control_plane(
    monkeypatch,
) -> None:
    monkeypatch.delattr(au_api, "compose_hosted_agent_control_plane", raising=False)

    result = doctor._check_a2a_persistence()

    assert result["status"] == "fail"
    assert result["data"] == {"ready": False, "redacted": True}


def test_a2a_doctor_passes_with_the_hosted_control_plane(monkeypatch) -> None:
    monkeypatch.setattr(
        au_api,
        "compose_hosted_agent_control_plane",
        lambda client, session: None,
        raising=False,
    )

    result = doctor._check_a2a_persistence()

    assert result["status"] == "ok"
    assert result["data"] == {"hosted_control_plane": True, "redacted": True}
