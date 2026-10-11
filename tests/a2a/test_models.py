"""Typed Agent Card capability-advertisement model (GRAPHOS-A2A-R009.1).

Proves the Agent Card never advertises streaming, push notifications, or
state-transition history unless a real durable authority is wired for it.
"""

from __future__ import annotations

import pytest

from graph_os.a2a.models import (
    A2AAgentCapabilities,
    A2APushNotificationAuthority,
    A2AStreamingAuthority,
    A2ATransitionHistoryAuthority,
)


class _StreamingAuthority:
    def resume_from_cursor(self, task_id: str, cursor: str | None) -> None:
        return None


class _PushAuthority:
    def register_push_target(self, task_id: str, target_url: str) -> None:
        return None


class _HistoryAuthority:
    def transition_history(self, task_id: str) -> list[object]:
        return []


class _NotAnAuthority:
    """Deliberately does not implement any capability protocol."""


@pytest.mark.spec("GRAPHOS-A2A-R009")
@pytest.mark.spec("GRAPHOS-A2A-R009.1")
def test_default_capabilities_are_all_false() -> None:
    capabilities = A2AAgentCapabilities()
    assert capabilities.streaming is False
    assert capabilities.push_notifications is False
    assert capabilities.state_transition_history is False


@pytest.mark.spec("GRAPHOS-A2A-R009")
@pytest.mark.spec("GRAPHOS-A2A-R009.1")
def test_from_wired_authorities_with_none_stays_false() -> None:
    capabilities = A2AAgentCapabilities.from_wired_authorities()
    assert capabilities.streaming is False
    assert capabilities.push_notifications is False
    assert capabilities.state_transition_history is False


@pytest.mark.spec("GRAPHOS-A2A-R009")
@pytest.mark.spec("GRAPHOS-A2A-R009.1")
def test_from_wired_authorities_advertises_only_what_is_wired() -> None:
    capabilities = A2AAgentCapabilities.from_wired_authorities(
        streaming_authority=_StreamingAuthority(),
    )
    assert capabilities.streaming is True
    assert capabilities.push_notifications is False
    assert capabilities.state_transition_history is False


@pytest.mark.spec("GRAPHOS-A2A-R009")
@pytest.mark.spec("GRAPHOS-A2A-R009.1")
def test_from_wired_authorities_all_three_wired() -> None:
    capabilities = A2AAgentCapabilities.from_wired_authorities(
        streaming_authority=_StreamingAuthority(),
        push_notification_authority=_PushAuthority(),
        transition_history_authority=_HistoryAuthority(),
    )
    assert capabilities.streaming is True
    assert capabilities.push_notifications is True
    assert capabilities.state_transition_history is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"streaming_authority": _NotAnAuthority()},
        {"push_notification_authority": _NotAnAuthority()},
        {"transition_history_authority": _NotAnAuthority()},
    ],
)
@pytest.mark.spec("GRAPHOS-A2A-R009")
@pytest.mark.spec("GRAPHOS-A2A-R009.1")
def test_from_wired_authorities_refuses_non_conforming_object(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(TypeError):
        A2AAgentCapabilities.from_wired_authorities(**kwargs)  # type: ignore[arg-type]


@pytest.mark.spec("GRAPHOS-A2A-R012.1")
def test_protocols_are_runtime_checkable_against_conforming_objects() -> None:
    assert isinstance(_StreamingAuthority(), A2AStreamingAuthority)
    assert isinstance(_PushAuthority(), A2APushNotificationAuthority)
    assert isinstance(_HistoryAuthority(), A2ATransitionHistoryAuthority)
    assert not isinstance(_NotAnAuthority(), A2AStreamingAuthority)
