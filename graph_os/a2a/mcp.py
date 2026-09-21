"""MCP/REST and native HTTP registration for the unary A2A facade."""

from __future__ import annotations

from typing import Any, Literal

from .application import AmbientA2AAuthenticator, create_a2a_handlers
from .authority import WorkItemA2AAuthority
from .routing import OrchestratorA2ARouter
from .service import A2AService

_SERVICE: A2AService | None = None


def _service() -> A2AService:
    global _SERVICE
    if _SERVICE is None:
        from graph_os.mcp_server import runtime

        provider = runtime._get_engine
        _SERVICE = A2AService(
            authority=WorkItemA2AAuthority(provider),
            router=OrchestratorA2ARouter(provider),
        )
    return _SERVICE


def register_a2a_tools(mcp: Any) -> None:
    """Register one action router and the protocol-native unary routes."""

    @mcp.tool(
        name="graph_a2a",
        description=(
            "Discover or manage governed unary A2A tasks. Actions: card, send, "
            "get, list, cancel. Streaming and push are not supported."
        ),
        tags={"graph-os", "a2a", "orchestration"},
    )
    async def graph_a2a(
        action: Literal["card", "send", "get", "list", "cancel"],
        message: dict[str, Any] | None = None,
        task_id: str = "",
        idempotency_key: str = "",
        cursor: str = "",
        limit: int = 50,
        context_budget_tokens: int = 0,
        endpoint_url: str = "/a2a",
    ) -> str:
        service = _service()
        if action == "card":
            result: Any = service.agent_card(endpoint_url)
        elif action == "send":
            if message is None:
                raise ValueError("message is required")
            result = await service.send_message(
                message=message,
                idempotency_key=idempotency_key,
                context_budget_tokens=context_budget_tokens or None,
            )
        elif action == "get":
            result = await service.get_task(task_id)
            if result is None:
                raise ValueError("A2A task not found")
        elif action == "list":
            result = await service.list_tasks(cursor=cursor or None, limit=limit)
        else:
            result = await service.cancel_task(task_id)
        return result.model_dump_json(by_alias=True)

    from graph_os.mcp_server import runtime

    runtime.REGISTERED_TOOLS["graph_a2a"] = graph_a2a
    card_handler, rpc_handler = create_a2a_handlers(
        service=_service(), authenticator=AmbientA2AAuthenticator()
    )
    mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])(card_handler)
    mcp.custom_route("/a2a", methods=["POST"])(rpc_handler)


__all__ = ["register_a2a_tools"]
