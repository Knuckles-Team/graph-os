"""Typed AU control-plane composition tests."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.api.agent_control_plane import GraphRlmRunRequest
from agent_utilities.knowledge_graph.core.session import GraphSession, resolve_session
from agent_utilities.security.brain_context import ActorContext, ActorType

from graph_os.mcp_server import runtime
from graph_os.mcp_server.agent_control import register_graph_rlm


class Mcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, name: str, **kwargs: Any):
        del kwargs

        def decorate(function: Any) -> Any:
            self.tools[name] = function
            return function

        return decorate


@pytest.fixture(autouse=True)
def restore_graph_rlm_registration():
    prior_tool = runtime.REGISTERED_TOOLS.get("graph_rlm")
    prior_route = runtime.ACTION_TOOL_ROUTES.get("graph_rlm")
    yield
    if prior_tool is None:
        runtime.REGISTERED_TOOLS.pop("graph_rlm", None)
    else:
        runtime.REGISTERED_TOOLS["graph_rlm"] = prior_tool
    if prior_route is None:
        runtime.ACTION_TOOL_ROUTES.pop("graph_rlm", None)
    else:
        runtime.ACTION_TOOL_ROUTES["graph_rlm"] = prior_route


def _verified_session() -> GraphSession:
    actor = ActorContext(
        actor_id="rlm-user",
        actor_type=ActorType.HUMAN,
        roles=("user",),
        tenant_id="tenant-a",
        authenticated=True,
    )
    return GraphSession(
        actor=actor,
        tenant="tenant-a",
        scopes=frozenset({"kg:read"}),
        graph="tenant-a",
        policy_version="policy-7",
        audience="graph-runtime",
    )


@pytest.mark.asyncio
async def test_graph_rlm_is_composed_from_injected_authorities() -> None:
    mcp = Mcp()
    client = object()
    session = _verified_session()
    calls: list[GraphRlmRunRequest] = []

    class ControlPlane:
        async def graph_rlm(self, request: GraphRlmRunRequest):
            calls.append(request)
            return SimpleNamespace(model_dump_json=lambda: '{"action":"run","ok":true}')

    def factory(actual_client: Any, actual_session: Any):
        assert actual_client is client
        assert actual_session is session
        assert resolve_session(actual_session) is session
        return ControlPlane()

    prior_tool = runtime.REGISTERED_TOOLS.get("graph_rlm")
    prior_route = runtime.ACTION_TOOL_ROUTES.get("graph_rlm")
    prior_process_session = runtime._PROCESS_SESSION
    runtime._PROCESS_SESSION = session
    try:
        register_graph_rlm(
            mcp,
            client_for_session=lambda actual_session: (
                client if actual_session is session else None
            ),
            factory=factory,
        )
        request = GraphRlmRunRequest(task="inspect", input_text="state")
        assert await mcp.tools["graph_rlm"](request) == ('{"action":"run","ok":true}')
        assert calls == [request]
        assert runtime.REGISTERED_TOOLS["graph_rlm"] is mcp.tools["graph_rlm"]
        assert runtime.ACTION_TOOL_ROUTES["graph_rlm"] == "/graph/rlm"
    finally:
        runtime._PROCESS_SESSION = prior_process_session
        if prior_tool is None:
            runtime.REGISTERED_TOOLS.pop("graph_rlm", None)
        else:
            runtime.REGISTERED_TOOLS["graph_rlm"] = prior_tool
        if prior_route is None:
            runtime.ACTION_TOOL_ROUTES.pop("graph_rlm", None)
        else:
            runtime.ACTION_TOOL_ROUTES["graph_rlm"] = prior_route


@pytest.mark.asyncio
async def test_graph_rlm_call_has_no_unverified_fallback() -> None:
    mcp = Mcp()

    @contextlib.contextmanager
    def missing_scope():
        raise PermissionError("verified authorities required")
        yield

    original_scope = runtime.verified_tool_session_scope
    runtime.verified_tool_session_scope = missing_scope
    try:
        register_graph_rlm(mcp, client_for_session=lambda session: object())
        with pytest.raises(PermissionError, match="verified authorities required"):
            await mcp.tools["graph_rlm"](
                GraphRlmRunRequest(task="inspect", input_text="state")
            )
    finally:
        runtime.verified_tool_session_scope = original_scope


@pytest.mark.asyncio
async def test_graph_rlm_rejects_mismatched_ambient_session() -> None:
    mcp = Mcp()

    @contextlib.contextmanager
    def mismatched_scope():
        raise PermissionError("Verified actor and GraphSession authority differ")
        yield

    original_scope = runtime.verified_tool_session_scope
    runtime.verified_tool_session_scope = mismatched_scope
    try:
        register_graph_rlm(mcp, client_for_session=lambda session: object())
        with pytest.raises(PermissionError, match="authority differ"):
            await mcp.tools["graph_rlm"](
                GraphRlmRunRequest(task="inspect", input_text="state")
            )
    finally:
        runtime.verified_tool_session_scope = original_scope
