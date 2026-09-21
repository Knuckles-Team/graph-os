"""Execution-port proof for the native A2A worker."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.a2a.worker import A2AExecutionResult, EpistemicGraphAgentWorker


class Execution:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> A2AExecutionResult:
        self.calls.append(kwargs)
        return A2AExecutionResult(context=[], artifacts=[], messages=[])


class Storage:
    def __init__(self) -> None:
        self.task = {
            "id": "task-1",
            "context_id": "context-1",
            "status": {"state": "submitted"},
        }
        self.updates: list[str] = []
        self.completed = False

    async def load_task(self, _task_id: str) -> dict[str, Any]:
        return self.task

    async def update_task(self, _task_id: str, *, state: str) -> None:
        self.updates.append(state)
        self.task["status"]["state"] = state

    async def load_context(self, _context_id: str) -> list[Any]:
        return []

    async def complete_task(self, *args: Any, **kwargs: Any) -> None:
        self.completed = True


@pytest.mark.asyncio
async def test_worker_executes_only_through_injected_port() -> None:
    execution = Execution()
    storage = Storage()
    worker = EpistemicGraphAgentWorker(
        execution=execution,
        broker=object(),
        storage=storage,
    )
    await worker.run_task({"id": "task-1"})
    assert storage.updates == ["working"]
    assert storage.completed is True
    assert execution.calls == [{"task": storage.task, "message_history": []}]
