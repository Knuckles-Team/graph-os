"""First-party connector agent host (ARBORA-HOST-R009, AU-BOUNDARY-R015).

A fleet connector's ``agent_server.py`` previously called
``agent_utilities.create_agent_server(...)`` to start a standalone HTTP/A2A
listener for its pydantic-graph agent -- the last ``agent_utilities`` import
in about 60 connectors. This module gives Arbora the same call signature so
a connector can depend on Arbora (already downstream of agent-utilities in
the composition order) instead of importing agent-utilities directly.

Scope of this first increment (the AU-BOUNDARY-R015 pilot slice): Arbora
owns the HTTP shell -- health, agent-card discovery, and a bounded A2A
``message/send`` exchange. Agent/model construction stays with
agent-utilities' public API (``agent_utilities.create_graph_agent`` /
``initialize_graph_from_workspace`` and ``agent_utilities.graph.execute_graph``),
which remains the agent orchestration authority per both repositories'
``AGENTS.md``. Deliberately NOT ported in this increment, matching
ARBORA-HOST-R009's own scope cut:

- the EG-backed broker/durable task store AU's ``protocols/a2a_epistemic``
  implements (a second task store is not created here either -- a task is a
  single bounded run, not a durable resource);
- the Agent Client Protocol adapter (stays with the agent runtime's harness
  integration per ARBORA-HOST-R009);
- the multi-worker gateway fork/spawn pool and the terminal UI launcher.

See ``plans/refactor/reconciliation-20261006/FLEET-SDK-MIGRATION-RECIPE.md``
for the connector migration recipe and the follow-up work this first slice
leaves open.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from arbora.agent_host.log_access import HostLogAccess
from arbora.agent_host.log_routes import register_host_log_routes
from arbora.agent_host.log_runtime import HostLogRuntime, register_host_log_runtime
from arbora.agent_host.start_admission import HostStartContext, check_host_start
from arbora.agent_host.start_configuration import (
    HostStartConfiguration,
    check_start_configuration,
)

logger = logging.getLogger(__name__)

__all__ = ["HostInterfaceUnavailable", "build_agent_host_app", "create_agent_server"]


class HostInterfaceUnavailable(RuntimeError):
    """An explicitly requested client interface has no authenticated handoff."""

    reason = "client_unavailable"


#: ``(graph, graph_config, query) -> result dict`` -- the default calls the
#: agent-utilities protocol-agnostic execution port; tests inject a stub so
#: they do not require a live model or MCP transport.
AgentExecutor = Callable[[Any, dict, str], dict]


def _default_executor(graph: Any, graph_config: dict, query: str) -> dict:
    """Run one query through agent-utilities' public execution port."""
    import asyncio

    from agent_utilities.graph import execute_graph

    return asyncio.run(execute_graph(graph, graph_config, query))


def _extract_text(message: dict[str, Any]) -> str:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("kind", "text") == "text"
    )


def _result_text(result: dict[str, Any]) -> str:
    for key in ("output", "content", "result", "response"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class _A2ARequest(BaseModel):
    """JSON-RPC envelope for the bounded ``/a2a`` exchange.

    Defined at module scope (not nested in :func:`build_agent_host_app`) so
    FastAPI's request-model resolution -- which, under
    ``from __future__ import annotations``, resolves a parameter's string
    annotation against the enclosing function's *module* globals -- can find
    it; a class nested inside the builder function is invisible to that
    lookup and silently falls back to treating the parameter as a query
    param instead of a JSON body.
    """

    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] | None = None
    id: int | str | None = None


def build_agent_host_app(
    *,
    graph: Any,
    graph_config: dict,
    name: str | None = None,
    description: str | None = None,
    executor: AgentExecutor | None = None,
    host_logs: HostLogAccess | None = None,
    host_log_runtime: HostLogRuntime | None = None,
):
    """Build the FastAPI app that hosts one connector agent.

    ``graph``/``graph_config`` are the bundle ``agent_utilities.create_graph_agent``
    or ``agent_utilities.initialize_graph_from_workspace`` already returns --
    callers keep using those AU entry points for construction; only the HTTP
    hosting moves here.
    """
    from fastapi import FastAPI

    run_executor = executor or _default_executor
    agent_name = name or "Agent"
    agent_description = description or ""

    app = FastAPI(title=agent_name)
    if host_log_runtime is not None:
        if host_logs is not None:
            raise ValueError("one host log composition required")
        host_logs = host_log_runtime.access
        register_host_log_runtime(app, host_log_runtime)
    register_host_log_routes(app, host_logs)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/agent-card.json")
    def agent_card() -> dict[str, Any]:
        return {
            "name": agent_name,
            "description": agent_description,
            "protocolVersion": "0.3.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "skills": [],
        }

    @app.post("/a2a")
    def a2a_rpc(request: _A2ARequest) -> dict[str, Any]:
        if request.method != "message/send":
            # No durable task store is created here (ARBORA-HOST-R009 does
            # not port AU's second broker), so there is nothing for
            # ``tasks/get``/``tasks/cancel`` to look up yet.
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32601, "message": "method not found"},
            }

        params = request.params or {}
        message = params.get("message")
        if not isinstance(message, dict):
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32602, "message": "invalid params"},
            }

        query = _extract_text(message)
        try:
            result = run_executor(graph, graph_config, query)
        except Exception as exc:  # noqa: BLE001 - A2A boundary is fail-closed
            logger.warning(
                "agent_host: execution failed (exception_type=%s)",
                type(exc).__name__,
            )
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32000, "message": "agent execution failed"},
            }

        reply_text = _result_text(result)
        task_id = str(uuid.uuid4())
        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "result": {
                "id": task_id,
                "kind": "task",
                "status": {"state": "completed"},
                "history": [
                    message,
                    {
                        "kind": "message",
                        "role": "agent",
                        "parts": [{"kind": "text", "text": reply_text}],
                        "messageId": str(uuid.uuid4()),
                    },
                ],
            },
        }

    return app


def create_agent_server(
    *,
    graph: Any | None = None,
    graph_config: dict | None = None,
    tag_prompts: dict[str, str] | None = None,
    tag_env_vars: dict[str, str] | None = None,
    graph_name: str = "GraphAgent",
    mcp_url: str | None = None,
    mcp_config: str | None = None,
    router_model: str | None = None,
    agent_model: str | None = None,
    min_confidence: float = 0.0,
    host: str | None = "127.0.0.1",
    port: int | None = 9000,
    name: str | None = None,
    system_prompt: str | None = None,
    workspace: str | None = None,
    executor: AgentExecutor | None = None,
    host_logs: HostLogAccess | None = None,
    host_log_runtime: HostLogRuntime | None = None,
    enable_terminal_ui: bool = False,
    enable_web_ui: bool | None = None,
    enable_web_logs: bool = False,
    host_start: HostStartContext | None = None,
    **_unused_au_hosting_kwargs: Any,
) -> None:
    """Build and serve a connector agent -- the Arbora-hosted replacement for
    ``agent_utilities.create_agent_server``.

    Call signature intentionally mirrors AU's: a connector's
    ``agent_server.py`` passes the same keyword arguments it already builds
    from ``create_agent_parser()``. Agent construction, when no ``graph``/
    ``graph_config`` bundle is supplied, still goes through AU's public graph
    builder (that is agent-utilities' retained authority, not a duplicate
    host); only the uvicorn/FastAPI serving shell is Arbora's own.

    ``**_unused_au_hosting_kwargs`` absorbs AU-hosting-only arguments a
    caller may still pass during migration (``debug``, ``enable_otel``,
    ``a2a_broker``, ...) that this first increment does not
    yet implement; see the migration recipe for current parity gaps. Notably
    ``provider``/``model_id``/``base_url``/``api_key`` (the default-LLM
    selection AU's own hosting shell forwards into its server config) land
    here too and are not yet wired into graph construction -- a tracked
    parity gap, not a silent behavior match.
    """
    selections = (enable_terminal_ui, enable_web_logs)
    if any(type(value) is not bool for value in selections) or (
        enable_web_ui is not None and type(enable_web_ui) is not bool
    ):
        raise ValueError("invalid_launch")
    if enable_terminal_ui or enable_web_ui or enable_web_logs:
        check_start_configuration(
            host_start,
            HostStartConfiguration(
                workspace=workspace,
                tag_prompts=tag_prompts,
                tag_env_vars=tag_env_vars,
                graph_name=graph_name,
                mcp_url=mcp_url,
                mcp_config=mcp_config,
                router_model=router_model,
                agent_model=agent_model,
                min_confidence=min_confidence,
                host=host,
                port=port,
                name=name,
                system_prompt=system_prompt,
                enable_terminal_ui=enable_terminal_ui,
                enable_web_ui=enable_web_ui,
                enable_web_logs=enable_web_logs,
            ),
            resources=(graph, graph_config, executor, host_logs, host_log_runtime),
            unused_arguments=_unused_au_hosting_kwargs,
        )
        check_host_start(
            host_start,
            workspace=workspace,
            terminal=enable_terminal_ui,
            web=enable_web_ui,
            web_logs=enable_web_logs,
        )
        raise HostInterfaceUnavailable("client_unavailable")

    if graph is None or graph_config is None:
        from agent_utilities.graph import (
            create_graph_agent,
            initialize_graph_from_workspace,
        )

        if tag_prompts:
            graph, graph_config = create_graph_agent(
                tag_prompts=tag_prompts,
                tag_env_vars=tag_env_vars,
                mcp_url=mcp_url,
                mcp_config=mcp_config,
                name=graph_name,
                router_model=router_model,
                agent_model=agent_model,
                min_confidence=min_confidence,
            )
        else:
            graph, graph_config = initialize_graph_from_workspace(
                mcp_config=mcp_config,
                router_model=router_model,
                agent_model=agent_model,
                workspace=workspace,
            )

    app = build_agent_host_app(
        graph=graph,
        graph_config=graph_config,
        name=name,
        description=system_prompt,
        executor=executor,
        host_logs=host_logs,
        host_log_runtime=host_log_runtime,
    )

    import uvicorn

    uvicorn.run(app, host=host or "127.0.0.1", port=port or 9000)
