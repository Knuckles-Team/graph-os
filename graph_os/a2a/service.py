"""Application service shared by A2A, MCP, and REST transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .authority import A2ATaskAuthority
from .models import A2AAgentCard, A2AListResult, A2AMessage, A2ASkill, A2ATask
from .routing import A2ARouter

__all__ = ["A2ACardMetadata", "A2AService"]


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
                    tags=["orchestration", "knowledge-graph", "unary"],
                    input_modes=["text/plain"],
                    output_modes=["text/plain"],
                )
            ],
        )
