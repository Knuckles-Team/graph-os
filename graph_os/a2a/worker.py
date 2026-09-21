"""A2A worker driven through the canonical Graph OS execution seam."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fasta2a.broker import TaskOperation
from fasta2a.schema import Artifact, Message, Task, TaskIdParams, TaskSendParams
from pydantic_ai.messages import ModelMessage

from .persistence import (
    _DELIVERY_CONTROL,
    _TERMINAL_STATES,
    A2AStorageConflict,
    EpistemicGraphA2ABroker,
    EpistemicGraphA2AStorage,
    _A2ADeliveryRetry,
)

__all__ = ["A2AExecutionPort", "A2AExecutionResult", "EpistemicGraphAgentWorker"]


@dataclass(frozen=True)
class A2AExecutionResult:
    """Normalized canonical execution result committed by the durable worker."""

    context: list[ModelMessage]
    artifacts: list[Artifact]
    messages: list[Message]


@runtime_checkable
class A2AExecutionPort(Protocol):
    """The sole agent-plane execution seam accepted by the A2A worker."""

    async def execute(
        self, *, task: Task, message_history: list[ModelMessage]
    ) -> A2AExecutionResult: ...


class EpistemicGraphAgentWorker:
    """Fenced worker which never owns or invokes an agent implementation."""

    def __init__(
        self,
        *,
        execution: A2AExecutionPort,
        broker: EpistemicGraphA2ABroker,
        storage: EpistemicGraphA2AStorage,
    ) -> None:
        if not isinstance(execution, A2AExecutionPort):
            raise TypeError("execution does not implement A2AExecutionPort")
        self.execution = execution
        self.broker = broker
        self.storage = storage

    async def _mark_failed(self, task_id: str) -> None:
        try:
            await self.storage.update_task(task_id, state="failed")
        except A2AStorageConflict:
            latest = await self.storage.load_task(task_id)
            if latest is not None and latest["status"]["state"] in _TERMINAL_STATES:
                return
            raise

    async def run_task(self, params: TaskSendParams) -> None:
        task = await self.storage.load_task(params["id"])
        if task is None:
            raise ValueError("A2A task is unavailable")
        if task["status"]["state"] in _TERMINAL_STATES:
            return
        if task["status"]["state"] not in {"submitted", "working"}:
            raise A2AStorageConflict("A2A task is not executable")
        await self.storage.update_task(task["id"], state="working")
        history = await self.storage.load_context(task["context_id"]) or []
        try:
            result = await self.execution.execute(task=task, message_history=history)
            await self.storage.complete_task(
                task["id"],
                result.context,
                new_artifacts=result.artifacts,
                new_messages=result.messages,
            )
        except asyncio.CancelledError:
            raise
        except A2AStorageConflict:
            latest = await self.storage.load_task(task["id"])
            if latest is None or latest["status"]["state"] not in _TERMINAL_STATES:
                raise
        except (RuntimeError, ValueError, TypeError):
            await self._mark_failed(task["id"])

    async def cancel_task(self, params: TaskIdParams) -> None:
        try:
            await self.storage.cancel_task(params["id"])
        except A2AStorageConflict:
            latest = await self.storage.load_task(params["id"])
            if latest is None or latest["status"]["state"] not in _TERMINAL_STATES:
                raise

    async def _handle(self, operation: TaskOperation) -> None:
        if operation["operation"] == "run":
            await self.run_task(operation["params"])
        elif operation["operation"] == "cancel":
            await self.cancel_task(operation["params"])
        else:
            raise RuntimeError("native A2A worker received an invalid operation")

    async def _loop(self) -> None:
        iterator: AsyncGenerator[TaskOperation, None] = (
            self.broker.receive_task_operations()
        )
        try:
            while True:
                operation = await anext(iterator)
                control = _DELIVERY_CONTROL.get()
                if control is None:
                    raise RuntimeError("native A2A delivery control is unavailable")
                handler = asyncio.create_task(self._handle(operation))
                abort = asyncio.create_task(control.abort_event.wait())
                done, _pending = await asyncio.wait(
                    {handler, abort}, return_when=asyncio.FIRST_COMPLETED
                )
                if abort in done and control.abort_reason:
                    handler.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await handler
                    if control.abort_reason not in {"task_canceled", "task_terminal"}:
                        with contextlib.suppress(_A2ADeliveryRetry):
                            await iterator.athrow(_A2ADeliveryRetry("delivery aborted"))
                        iterator = self.broker.receive_task_operations()
                    continue
                abort.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await abort
                if handler.exception() is not None:
                    with contextlib.suppress(_A2ADeliveryRetry):
                        await iterator.athrow(
                            _A2ADeliveryRetry("handler did not commit")
                        )
                    iterator = self.broker.receive_task_operations()
        finally:
            with contextlib.suppress(BaseException):
                await iterator.aclose()

    @contextlib.asynccontextmanager
    async def run(self):
        loop = asyncio.create_task(self._loop(), name="a2a-execution-worker")
        try:
            yield self
        finally:
            loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop
