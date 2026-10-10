"""Governed agent, durable A2A task, and RLM operation declarations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from graph_os.api.ops._common import Params
from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    Idempotency,
    OpSpec,
    Verb,
)


class AgentRunParams(Params):
    message: dict[str, Any]
    context_budget_tokens: int | None = Field(default=None, ge=256, le=1_000_000)


class TaskRefParams(Params):
    task_id: str = Field(min_length=1, max_length=80)


class TaskListParams(Params):
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=50, ge=1, le=100)


class RlmParams(Params):
    request: dict[str, Any]


class AgentResult(Params):
    value: dict[str, Any]


async def handle_agent(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Use the serving A2A authority, which owns task and idempotency state."""
    from agent_utilities.api import use_session

    if context.caller.session is None:
        raise RuntimeError("agent operation requires a verified session")
    service = context.services.get("a2a")
    if service is None:
        raise RuntimeError("A2A authority is not bound")
    with use_session(context.caller.session):
        if op.id == "agents.run":
            if not context.idempotency_key:
                raise ValueError("Idempotency-Key is required")
            result = await service.send_message(
                message=params["message"],
                idempotency_key=context.idempotency_key,
                context_budget_tokens=params.get("context_budget_tokens"),
            )
        elif op.id == "agents.tasks.get":
            result = await service.get_task(params["task_id"])
            if result is None:
                raise LookupError("A2A task not found")
        elif op.id == "agents.tasks.list":
            result = await service.list_tasks(
                cursor=params.get("cursor"), limit=params["limit"]
            )
        elif op.id == "agents.tasks.cancel":
            result = await service.cancel_task(params["task_id"])
        else:
            raise ValueError("unknown agent operation")
    return {"value": result.model_dump(mode="json", by_alias=True)}


async def handle_rlm(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Compose AU's public RLM control plane with the verified caller context."""
    from agent_utilities.api import GraphRlmRequest, compose_agent_control_plane
    from pydantic import TypeAdapter

    if context.caller.session is None:
        raise RuntimeError("RLM operation requires a verified session")
    request = TypeAdapter(GraphRlmRequest).validate_python(params["request"])
    action = op.id.rsplit(".", 1)[-1]
    if request.action != action:
        raise ValueError("RLM action does not match operation")
    control_plane = compose_agent_control_plane(context.client, context.caller.session)
    result = await control_plane.graph_rlm(request)
    return {"value": result.model_dump(mode="json")}


def operations() -> tuple[OpSpec, ...]:
    """Operation records consumed by the central registry and all projections."""
    handler = Composite(handler="graph_os.api.ops.agents.handle_agent")
    rlm_handler = Composite(handler="graph_os.api.ops.agents.handle_rlm")
    return (
        OpSpec(
            id="agents.run",
            verb=Verb.ACT,
            summary="Run a governed agent task",
            examples=("run an agent to investigate this issue",),
            params=AgentRunParams,
            result=AgentResult,
            binding=handler,
            scopes=frozenset({"kg:write"}),
            effect=Effect.WRITE,
            idempotency=Idempotency.KEY_REQUIRED,
            audit=AuditClass.EVENT,
        ),
        OpSpec(
            id="agents.tasks.get",
            verb=Verb.ASK,
            summary="Get an owned agent task",
            examples=("show the status of this agent task",),
            params=TaskRefParams,
            result=AgentResult,
            binding=handler,
            scopes=frozenset({"kg:read"}),
        ),
        OpSpec(
            id="agents.tasks.list",
            verb=Verb.ASK,
            summary="List owned agent tasks",
            examples=("list my agent tasks",),
            params=TaskListParams,
            result=AgentResult,
            binding=handler,
            scopes=frozenset({"kg:read"}),
        ),
        OpSpec(
            id="agents.tasks.cancel",
            verb=Verb.ACT,
            summary="Cancel an owned agent task",
            examples=("cancel this agent task",),
            params=TaskRefParams,
            result=AgentResult,
            binding=handler,
            scopes=frozenset({"kg:write"}),
            effect=Effect.WRITE,
            audit=AuditClass.EVENT,
        ),
        *(
            OpSpec(
                id=f"rlm.{action}",
                verb=Verb.ACT,
                summary=f"Execute governed RLM {action.replace('_', ' ')}",
                examples=(f"{action.replace('_', ' ')} with RLM",),
                params=RlmParams,
                result=AgentResult,
                binding=rlm_handler,
                scopes=frozenset({"kg:write"}),
                effect=Effect.WRITE,
                audit=AuditClass.EVENT,
            )
            for action in ("run", "benchmark", "evolve_prompt")
        ),
    )


specs = operations
