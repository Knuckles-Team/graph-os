"""The GraphOS inbox reaper keeps failed deliveries pending for another attempt."""

from __future__ import annotations

from typing import Any

import pytest
from agent_utilities.api import messaging as au_messaging_api
from agent_utilities.core import config
from agent_utilities.messaging.models import SendResult

from graph_os.messaging import inbox
from graph_os.messaging.router import InboundRouter
from graph_os.messaging.service import MessagingService


@pytest.mark.asyncio
@pytest.mark.parametrize("delivered", [False, True])
async def test_reaper_uses_au_reply_port_and_preserves_send_result(
    monkeypatch: pytest.MonkeyPatch, delivered: bool
) -> None:
    engine = object()
    router = InboundRouter()
    router._running = True
    seen: list[Any] = []

    class Backend:
        id = "telegram"

        async def send_message(self, channel_id: str, reply: str) -> SendResult:
            seen.append((channel_id, reply))
            return SendResult(
                success=delivered, platform=self.id, channel_id=channel_id
            )

    class Service:
        def _resolve_engine(self) -> object:
            return engine

    async def reply(bound_engine: object, text: str, *, session: str) -> str:
        seen.append((bound_engine, text, session))
        return "reply text"

    async def retry(bound_engine: object, send: Any) -> int:
        assert bound_engine is engine
        seen.append(
            await send(
                {
                    "platform": "telegram",
                    "channel_id": "42",
                    "text": "hello",
                    "session": "messaging:telegram:42",
                }
            )
        )
        router._running = False
        return int(delivered)

    router._backends.append(Backend())
    monkeypatch.setattr(config, "setting", lambda *_args: "0")
    monkeypatch.setattr(MessagingService, "instance", lambda: Service())
    monkeypatch.setattr(au_messaging_api, "graph_agent_reply", reply)
    monkeypatch.setattr(inbox, "retry_unanswered", retry)

    await router._inbox_reaper_loop()
    assert seen == [
        (engine, "hello", "messaging:telegram:42"),
        ("42", "reply text"),
        delivered,
    ]
