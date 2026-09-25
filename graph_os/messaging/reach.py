"""GraphOS outbound notification adapter for agent control-plane callers."""

from __future__ import annotations

from typing import Any

from agent_utilities.messaging.models import SendResult
from agent_utilities.messaging.service import MessagingService, reach_user_sync


def notify_sync(engine: Any, text: str, **kwargs: Any) -> SendResult:
    """Bind the served graph authority and deliver through governed messaging."""
    MessagingService.instance(engine)
    return reach_user_sync(text, **kwargs)
