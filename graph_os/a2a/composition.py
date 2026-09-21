"""Composition hooks for the Graph OS unary A2A facade."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fasta2a.schema import Skill, Task
from pydantic_ai.messages import ModelMessage

from .application import A2AAuthenticator, create_a2a_application
from .persistence import (
    EpistemicGraphA2ABroker,
    EpistemicGraphA2ARuntime,
    EpistemicGraphA2AStorage,
)
from .service import A2AService
from .worker import A2AExecutionPort, A2AExecutionResult, EpistemicGraphAgentWorker

__all__ = ["A2AComposition", "CallableExecutionPort", "compose_a2a"]


@dataclass(frozen=True)
class CallableExecutionPort:
    """Adapter from the canonical AU/Graph OS task executor to the A2A port."""

    executor: Callable[..., Awaitable[A2AExecutionResult]]

    async def execute(
        self, *, task: Task, message_history: list[ModelMessage]
    ) -> A2AExecutionResult:
        result = await self.executor(task=task, message_history=message_history)
        if not isinstance(result, A2AExecutionResult):
            raise TypeError("canonical execution seam returned an invalid A2A result")
        return result


@dataclass(frozen=True)
class A2AComposition:
    application: Any
    service: A2AService
    worker: EpistemicGraphAgentWorker
    broker: EpistemicGraphA2ABroker
    storage: EpistemicGraphA2AStorage


def compose_a2a(
    *,
    config: Any,
    authenticator: A2AAuthenticator,
    execution: A2AExecutionPort,
    name: str,
    description: str,
    version: str,
    endpoint_url: str,
    skills: Sequence[Skill] = (),
    client: Any | None = None,
    session: Any | None = None,
) -> A2AComposition:
    """Compose persistence, application service, worker, and unary transport."""
    if (
        config.a2a_broker != "epistemic_graph"
        or config.a2a_storage != "epistemic_graph"
    ):
        raise ValueError(
            "A2A_BROKER and A2A_STORAGE must both select 'epistemic_graph'"
        )
    runtime = EpistemicGraphA2ARuntime(client=client, session=session)
    storage = EpistemicGraphA2AStorage(
        runtime,
        max_payload_bytes=config.a2a_max_payload_bytes,
        max_history=config.a2a_max_history,
        max_artifacts=config.a2a_max_artifacts,
        max_context_messages=config.a2a_max_context_messages,
        update_retries=config.a2a_storage_update_retries,
    )
    broker = EpistemicGraphA2ABroker(
        runtime,
        storage,
        poll_interval_ms=config.a2a_broker_poll_interval_ms,
        lease_ms=config.a2a_broker_lease_ms,
        prefetch=config.a2a_broker_prefetch,
        max_payload_bytes=config.a2a_max_payload_bytes,
        message_ttl_ms=config.a2a_broker_message_ttl_ms,
        max_delivery_count=config.a2a_broker_max_delivery_count,
        reconcile_interval_ms=config.a2a_dispatch_reconcile_interval_ms,
        reconcile_limit=config.a2a_dispatch_reconcile_limit,
        cancellation_poll_interval_ms=config.a2a_cancellation_poll_interval_ms,
    )
    service = A2AService(storage=storage, broker=broker)
    worker = EpistemicGraphAgentWorker(
        execution=execution, broker=broker, storage=storage
    )
    application = create_a2a_application(
        service=service,
        authenticator=authenticator,
        name=name,
        description=description,
        version=version,
        endpoint_url=endpoint_url,
        skills=skills,
    )
    return A2AComposition(application, service, worker, broker, storage)
