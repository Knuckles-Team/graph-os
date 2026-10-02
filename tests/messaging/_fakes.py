"""Shared lightweight test doubles for messaging backend tests."""

from __future__ import annotations

from typing import Any

from agent_utilities.messaging.models import SendResult


class FakeMessagingBackend:
    """Minimal connected backend that records sends and supports reply_to."""

    def __init__(self, platform: str = "telegram") -> None:
        self.id = platform
        self._connected = True
        self.sent: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str, str]] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_message(self, channel_id: str, text: str, **_: Any) -> SendResult:
        self.sent.append((channel_id, text))
        return SendResult(success=True, platform=self.id, channel_id=channel_id)

    async def reply_to(
        self, channel_id: str, message_id: str, text: str, **_: Any
    ) -> SendResult:
        self.replies.append((channel_id, message_id, text))
        return SendResult(success=True, platform=self.id, channel_id=channel_id)
