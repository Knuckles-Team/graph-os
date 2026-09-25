"""Background work on the serving loop under the process authority.

Controllers that run for the server's lifetime (the error-budget throttle, the
finance schedule) share two pieces: a FastMCP extension that runs one loop
coroutine on the serving loop and cancels it at shutdown, and the process
authority -- the tenant's session-routed EG client bound to the process
session's verified context, actor and session.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from typing import Any

from fastmcp.server.extensions import ServerExtension

__all__ = ["BackgroundLoopExtension", "process_authority"]


class BackgroundLoopExtension(ServerExtension):
    """Run one loop coroutine on the serving loop for the server's lifetime."""

    identifier = "graph-os/background-loop"

    def __init__(self, run: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._run = run

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        task: asyncio.Task[None] = asyncio.create_task(self._run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def process_authority(
    session: Any, client_for: Callable[[str], Any]
) -> Callable[[], contextlib.AbstractContextManager[Any]]:
    """A context factory yielding the process session's verified EG client."""
    tenant = str(session.tenant)

    @contextlib.contextmanager
    def authority() -> Iterator[Any]:
        from agent_utilities.api.session import use_session
        from agent_utilities.security.brain_context import use_actor

        client = client_for(tenant)
        with (
            use_actor(session.actor),
            use_session(session),
            client.use_verified_context(session.engine_verified_context()),
        ):
            yield client

    return authority
