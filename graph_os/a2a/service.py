"""Application service shared by A2A, MCP, and REST transports."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .authority import A2ATaskAuthority
from .models import (
    A2AAgentCard,
    A2AListResult,
    A2AMessage,
    A2ASkill,
    A2ATask,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)
from .routing import A2ARouter

__all__ = [
    "A2ACardMetadata",
    "A2AService",
    "StreamPolicy",
    "event_id",
    "state_fence",
]

StreamEvent = A2ATask | A2ATaskStatusUpdateEvent
_FINAL_STATES = frozenset({"completed", "canceled", "failed", "rejected"})


@dataclass(frozen=True)
class StreamPolicy:
    """Bounds for one task-following stream.

    The durable WorkItem is the only state: a stream polls it through the
    authority, emits each transition once, and ends at a final state or at
    ``max_duration_s`` (the client then resubscribes with its last event id).
    """

    poll_interval_s: float = 0.5
    max_interval_s: float = 2.0
    max_duration_s: float = 900.0
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic


def state_fence(task_id: str, status: A2ATaskStatus) -> str:
    """The resumable SSE event id for one observed task state."""
    return f"{task_id}:{status.state}:{status.timestamp or ''}"


def event_id(task: A2ATask) -> str:
    """The resumable fence for one observed task state."""
    return state_fence(task.id, task.status)


def _status_event(task: A2ATask, *, final: bool) -> A2ATaskStatusUpdateEvent:
    return A2ATaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=task.status,
        final=final,
        metadata=task.metadata,
    )


@dataclass(frozen=True)
class A2ACardMetadata:
    name: str = "GraphOS"
    description: str = "Governed Agent OS task delegation"
    version: str = "1.0.0"


@dataclass(frozen=True)
class A2AService:
    authority: A2ATaskAuthority
    router: A2ARouter
    card_metadata: A2ACardMetadata = A2ACardMetadata()
    stream_policy: StreamPolicy = field(default_factory=StreamPolicy)

    async def send_message(
        self,
        *,
        message: A2AMessage | dict[str, Any],
        idempotency_key: str,
        context_budget_tokens: int | None = None,
    ) -> A2ATask:
        parsed = (
            message
            if isinstance(message, A2AMessage)
            else A2AMessage.model_validate(message)
        )
        decision = await self.router.route(
            parsed, context_budget_tokens=context_budget_tokens
        )
        return await self.authority.dispatch(
            message=parsed,
            idempotency_key=idempotency_key,
            decision=decision,
        )

    async def get_task(self, task_id: str) -> A2ATask | None:
        return await self.authority.get(task_id)

    async def list_tasks(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> A2AListResult:
        tasks, next_cursor = await self.authority.list(cursor=cursor, limit=limit)
        return A2AListResult(tasks=tasks, next_cursor=next_cursor)

    async def cancel_task(self, task_id: str) -> A2ATask:
        return await self.authority.cancel(task_id)

    async def stream_message(
        self,
        *,
        message: A2AMessage | dict[str, Any],
        idempotency_key: str,
        context_budget_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Admit one task, then follow it until a final state or the bound."""
        task = await self.send_message(
            message=message,
            idempotency_key=idempotency_key,
            context_budget_tokens=context_budget_tokens,
        )
        yield task
        async for event in self._follow(task, seen=event_id(task)):
            yield event

    async def resubscribe(
        self, task_id: str, *, last_event_id: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        """Re-attach to an owned task; skip the state ``last_event_id`` saw."""
        task = await self.get_task(task_id)
        if task is None:
            raise LookupError("A2A task not found")
        if event_id(task) != last_event_id:
            yield _status_event(task, final=task.status.state in _FINAL_STATES)
        async for event in self._follow(task, seen=event_id(task)):
            yield event

    async def _follow(self, task: A2ATask, *, seen: str) -> AsyncIterator[StreamEvent]:
        policy = self.stream_policy
        deadline = policy.clock() + policy.max_duration_s
        interval = policy.poll_interval_s
        current = task
        while current.status.state not in _FINAL_STATES:
            if policy.clock() >= deadline:
                return
            await policy.sleep(interval)
            latest = await self.get_task(task.id)
            if latest is None:
                raise LookupError("A2A task is no longer visible")
            if event_id(latest) != seen:
                seen = event_id(latest)
                interval = policy.poll_interval_s
                yield _status_event(latest, final=latest.status.state in _FINAL_STATES)
            else:
                interval = min(interval * 2, policy.max_interval_s)
            current = latest

    def agent_card(self, endpoint_url: str) -> A2AAgentCard:
        metadata = self.card_metadata
        return A2AAgentCard(
            name=metadata.name,
            description=metadata.description,
            version=metadata.version,
            url=endpoint_url,
            skills=[
                A2ASkill(
                    id="graph-os-delegate",
                    name="Governed task delegation",
                    description=(
                        "Route a text task to an authorized existing agent and "
                        "manage its durable WorkItem lifecycle."
                    ),
                    tags=["orchestration", "knowledge-graph", "streaming"],
                    input_modes=["text/plain"],
                    output_modes=["text/plain"],
                )
            ],
        )
