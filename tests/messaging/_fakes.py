"""Shared lightweight test doubles for messaging backend tests."""

from __future__ import annotations

from typing import Any

from agent_utilities.messaging.models import SendResult


def synthetic_token(*parts: str) -> str:
    """Build a deterministic, scanner-safe test-only token from fragments.

    No single line in this tree should carry a quoted credential-shaped
    literal; joining fragments at import time keeps each fixture obviously
    synthetic to a reader while never matching a secret-shaped pattern in
    any commit's patch text.
    """
    return "".join(parts)


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
