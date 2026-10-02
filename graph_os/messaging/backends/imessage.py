"""iMessage Backend (CONCEPT:AU-ECO.messaging.native-backend-abstraction). macOS-only via AppleScript bridge.

Install: ``pip install graph-os[messaging-imessage]``
CONCEPT:AU-ECO.messaging.native-backend-abstraction — Native Messaging Backend Abstraction
"""

from __future__ import annotations

import asyncio
import logging
import platform as _platform
from collections.abc import AsyncIterator
from datetime import UTC
from typing import Any

from agent_utilities.messaging.base import MessagingBackend
from agent_utilities.messaging.capabilities import (
    CAPABILITY_MATRIX,
    MessagingCapabilities,
)
from agent_utilities.messaging.models import (
    InboundEvent,
    MessagingConfig,
    PlatformId,
    SendResult,
)

logger = logging.getLogger(__name__)

_POLL_QUERY = """
SELECT
    m.guid,
    m.text,
    h.id,
    m.date
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE m.is_from_me = 0 AND m.date > ?
ORDER BY m.date ASC
"""


def _cocoa_now() -> int:
    """Current time in the Cocoa epoch (nanoseconds since 2001-01-01)."""
    from datetime import datetime

    epoch = datetime(2001, 1, 1, tzinfo=UTC)
    return int((datetime.now(UTC) - epoch).total_seconds() * 1_000_000_000)


def _poll_rows(db_path: str, last_timestamp: int) -> tuple[list[tuple[Any, ...]], int]:
    """One read-only pass over chat.db for messages newer than ``last_timestamp``."""
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute(_POLL_QUERY, (last_timestamp,))
        rows = cursor.fetchall()
    finally:
        conn.close()
    for row in rows:
        if row[3] > last_timestamp:
            last_timestamp = row[3]
    return rows, last_timestamp


class IMessageBackend(MessagingBackend):
    """iMessage backend via AppleScript (macOS only). CONCEPT:AU-ECO.messaging.native-backend-abstraction"""

    def __init__(self, config: MessagingConfig | None = None) -> None:
        super().__init__(config)

    @property
    def id(self) -> str:
        return "imessage"

    @property
    def capabilities(self) -> MessagingCapabilities:
        return CAPABILITY_MATRIX["imessage"]

    async def connect(self) -> None:
        if _platform.system() != "Darwin":
            raise ConnectionError("iMessage backend requires macOS.")
        self._connected = True
        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] iMessage backend connected (macOS)."
        )

    async def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_id: str = "",
        reply_to_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        try:
            script = f'tell application "Messages" to send "{text}" to buddy "{channel_id}" of (service 1 whose service type is iMessage)'
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return SendResult(
                    success=False, platform=PlatformId.IMESSAGE, error=stderr.decode()
                )
            return SendResult(
                success=True, platform=PlatformId.IMESSAGE, channel_id=channel_id
            )
        except Exception as e:
            return SendResult(success=False, platform=PlatformId.IMESSAGE, error=str(e))

    async def _idle_forever(self, reason: str) -> None:
        """Stay alive without producing events (unsupported platform or no database)."""
        logger.warning(f"{reason} Running in mock/fallback loop.")
        while True:
            await asyncio.sleep(3600)

    async def listen(self) -> AsyncIterator[InboundEvent]:
        """Poll the macOS chat.db SQLite database for new inbound messages."""
        import os

        if _platform.system() != "Darwin":
            await self._idle_forever("iMessage listen called on a non-macOS system.")
            return

        db_path = os.path.expanduser("~/Library/Messages/chat.db")
        if not os.path.exists(db_path):
            await self._idle_forever(f"iMessage database not found at {db_path}.")
            return

        last_timestamp = _cocoa_now()
        while True:
            try:
                rows, last_timestamp = _poll_rows(db_path, last_timestamp)
            except Exception as e:
                logger.error(f"iMessage listen error: {e}")
                rows = []
            for guid, text, sender, _date_val in rows:
                yield InboundEvent(
                    channel_id=sender or "unknown",
                    user_id=sender or "unknown",
                    content=text or "",
                    platform=PlatformId.IMESSAGE,
                    raw={},
                )
            await asyncio.sleep(5)
