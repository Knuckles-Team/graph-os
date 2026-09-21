"""Internal connection contract behind agent-webui's BrowserControlPort."""

from __future__ import annotations

from typing import Any, Protocol

from graph_os.browser_control.browser_control_api import (
    CatalogRegistrationReceipt,
)
from graph_os.browser_control.browser_control_client import BrowserClientMessage


class BrowserControlConnection(Protocol):
    async def receive(
        self, message: BrowserClientMessage | dict[str, Any]
    ) -> CatalogRegistrationReceipt | None: ...

    async def disconnect(self, reason: str) -> None: ...


__all__ = ["BrowserControlConnection"]
