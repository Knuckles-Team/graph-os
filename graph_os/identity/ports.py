"""Narrow identity contract consumed by the optional WebUI host."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class ServedIdentityPort(Protocol):
    async def prepare(self, bind_hosts: Iterable[str]) -> str | None: ...

    def webui_session_boundary(self) -> Callable[[Any], None]: ...
