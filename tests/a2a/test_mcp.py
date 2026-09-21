"""A2A MCP registration, discovery, and REST-parity contract."""

from __future__ import annotations

import json
from typing import Any

import pytest

from graph_os.a2a import mcp as a2a_mcp
from graph_os.a2a.models import A2ATask, A2ATaskStatus
from graph_os.a2a.service import A2AService
from graph_os.mcp_server import runtime


class _Mcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.routes: dict[tuple[str, tuple[str, ...]], Any] = {}

    def tool(self, *, name: str, **_kwargs: Any) -> Any:
        def decorate(function: Any) -> Any:
            self.tools[name] = function
            return function

        return decorate

    def custom_route(self, path: str, *, methods: list[str]) -> Any:
        def decorate(function: Any) -> Any:
            self.routes[(path, tuple(methods))] = function
            return function

        return decorate


@pytest.mark.asyncio
async def test_registration_exposes_exact_mcp_rest_and_native_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = A2ATask(
        id="a2a-" + "1" * 64,
        context_id="a2a-context-" + "2" * 64,
        status=A2ATaskStatus(state="working"),
    )

    class Authority:
        async def get(self, task_id: str) -> A2ATask | None:
            return task if task_id == task.id else None

    service = A2AService(authority=Authority(), router=object())  # type: ignore[arg-type]
    monkeypatch.setattr(a2a_mcp, "_service", lambda: service)
    mcp = _Mcp()
    prior = runtime.REGISTERED_TOOLS.get("graph_a2a")
    try:
        a2a_mcp.register_a2a_tools(mcp)
        assert runtime.REGISTERED_TOOLS["graph_a2a"] is mcp.tools["graph_a2a"]
        assert runtime.ACTION_TOOL_ROUTES["graph_a2a"] == "/graph/a2a"
        assert set(mcp.routes) == {
            ("/.well-known/agent-card.json", ("GET",)),
            ("/a2a", ("POST",)),
        }
        payload = json.loads(
            await mcp.tools["graph_a2a"](action="get", task_id=task.id)
        )
        assert payload["id"] == task.id
        card = json.loads(await mcp.tools["graph_a2a"](action="card"))
        assert card["capabilities"]["streaming"] is False
    finally:
        if prior is None:
            runtime.REGISTERED_TOOLS.pop("graph_a2a", None)
        else:
            runtime.REGISTERED_TOOLS["graph_a2a"] = prior
