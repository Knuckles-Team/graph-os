"""Process binding for the one multiplexer actually served by GraphOS.

The FastMCP transport and WebUI ASGI co-service run on different event loops.
Child sessions, refresh probes, and the immutable catalog swap all belong to
FastMCP's serving loop. This module provides one async-only command path into
that loop: callers await a concurrent future; they never synchronously wait on
another loop and never construct a detached multiplexer or catalog cache.

CONCEPT:AU-ECO.multiplexer.tool-gateway-catalog
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

from fastmcp.server.extensions import ServerExtension

from graph_os.fleet.multiplexer import MCPMultiplexer

__all__ = [
    "ServedMultiplexerBindingError",
    "ServedMultiplexerLoopExtension",
    "bind_served_multiplexer",
    "claim_served_multiplexer_loop",
    "get_served_multiplexer",
    "run_on_served_multiplexer",
]

_T = TypeVar("_T")


class ServedMultiplexerBindingError(RuntimeError):
    """The serving authority or its owner loop is unavailable."""


class ServedMultiplexerLoopExtension(ServerExtension):
    """Claim the served multiplexer loop as part of FastMCP startup."""

    identifier = "graph-os/fleet-loop"

    def __init__(self, multiplexer: MCPMultiplexer) -> None:
        self._multiplexer = multiplexer

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        claim_served_multiplexer_loop(self._multiplexer)
        yield


@dataclass(slots=True)
class _ServedMultiplexer:
    multiplexer: MCPMultiplexer
    owner_loop: asyncio.AbstractEventLoop | None = None

    def claim_running_loop(self) -> None:
        """Bind once to the loop currently executing a served MCP request."""
        loop = asyncio.get_running_loop()
        if self.owner_loop is not None and self.owner_loop is not loop:
            raise ServedMultiplexerBindingError(
                "served MCP catalog authority is already owned by another event loop"
            )
        self.owner_loop = loop

    async def run(
        self, operation: Callable[[MCPMultiplexer], Coroutine[object, object, _T]]
    ) -> _T:
        """Run ``operation`` on the owner loop without synchronously bridging."""
        owner = self.owner_loop
        if owner is None or owner.is_closed() or not owner.is_running():
            raise ServedMultiplexerBindingError(
                "served MCP catalog event loop is not available"
            )
        if asyncio.get_running_loop() is owner:
            return await operation(self.multiplexer)

        completion: Future[_T] = Future()
        caller_context = contextvars.copy_context()

        def submit() -> None:
            if completion.cancelled():
                return
            task: asyncio.Task[_T] = owner.create_task(
                operation(self.multiplexer), context=caller_context
            )

            def complete(done: asyncio.Task[_T]) -> None:
                if completion.cancelled():
                    return
                try:
                    completion.set_result(done.result())
                except BaseException as exc:
                    completion.set_exception(exc)

            task.add_done_callback(complete)

        owner.call_soon_threadsafe(submit, context=caller_context)
        return await asyncio.wrap_future(completion)


_served: _ServedMultiplexer | None = None


def _require_served() -> _ServedMultiplexer:
    if _served is None:
        raise ServedMultiplexerBindingError("served MCP catalog authority is not bound")
    return _served


def _assert_same_multiplexer(
    binding: _ServedMultiplexer, multiplexer: MCPMultiplexer
) -> None:
    if binding.multiplexer is not multiplexer:
        raise ServedMultiplexerBindingError(
            "a different MCP catalog authority is already bound"
        )


def bind_served_multiplexer(multiplexer: MCPMultiplexer) -> None:
    """Bind exactly one process-local served authority, idempotently."""
    global _served
    if _served is None:
        _served = _ServedMultiplexer(multiplexer)
        return
    _assert_same_multiplexer(_served, multiplexer)


def claim_served_multiplexer_loop(multiplexer: MCPMultiplexer) -> None:
    """Claim the current served request loop for the bound multiplexer."""
    binding = _require_served()
    _assert_same_multiplexer(binding, multiplexer)
    binding.claim_running_loop()


async def get_served_multiplexer() -> MCPMultiplexer:
    """Return the raw authority only while executing on its owner loop."""
    binding = _require_served()
    if binding.owner_loop is not asyncio.get_running_loop():
        raise ServedMultiplexerBindingError(
            "raw served MCP catalog access is restricted to its owner loop"
        )
    return binding.multiplexer


async def run_on_served_multiplexer[T](
    operation: Callable[[MCPMultiplexer], Coroutine[object, object, T]],
) -> T:
    """Submit one async operation to the exact served multiplexer instance."""
    return await _require_served().run(operation)


def _reset_served_multiplexer_for_tests() -> None:
    """Test-only reset for independent application compositions."""
    global _served
    _served = None
