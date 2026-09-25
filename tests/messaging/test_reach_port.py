"""AU notifications resolve the GraphOS serving port without a code import."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.core.goal_sla import _escalate
from agent_utilities.messaging import reach_port


def test_notification_port_fails_closed_without_one_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reach_port.metadata, "entry_points", lambda **_kw: [])

    with pytest.raises(LookupError, match="exactly one"):
        reach_port.notification_port()


def test_graphos_supplies_reach_service_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Service:
        def configured_platforms(self) -> list[str]:
            return ["telegram"]

        async def reach_user(self, text: str, **kwargs: Any) -> Any:
            return SimpleNamespace(success=True, platform="telegram")

        async def reach_user_and_wait(self, text: str, **kwargs: Any) -> str:
            return f"reply to {text}"

    from graph_os.messaging import reach

    service = Service()
    monkeypatch.setattr(reach.MessagingService, "instance", lambda: service)
    entry = SimpleNamespace(name="service", load=lambda: reach.service_port)
    monkeypatch.setattr(reach_port.metadata, "entry_points", lambda **_kw: [entry])

    assert reach_port.reach_service_port() is service


def test_goal_sla_uses_graphos_notification_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graph_os.messaging import reach

    engine = object()
    calls: list[tuple[Any, ...]] = []
    entry = SimpleNamespace(name="notify_sync", load=lambda: reach.notify_sync)
    monkeypatch.setattr(reach_port.metadata, "entry_points", lambda **_kw: [entry])
    monkeypatch.setattr(
        reach.MessagingService,
        "instance",
        lambda supplied_engine: calls.append(("engine", supplied_engine)),
    )
    monkeypatch.setattr(
        reach,
        "reach_user_sync",
        lambda text, **kwargs: calls.append(("send", text, kwargs)),
    )

    _escalate(
        engine,
        {"id": "g-1", "objective": "finish migration", "escalate_to": "owner-1"},
        {"age_seconds": 20, "sla_seconds": 10},
    )

    assert calls[0] == ("engine", engine)
    assert calls[1][0] == "send"
    assert "finish migration" in calls[1][1]
    assert calls[1][2] == {
        "user_id": "owner-1",
        "source": "goal_sla",
        "reason": "sla_breach",
    }
