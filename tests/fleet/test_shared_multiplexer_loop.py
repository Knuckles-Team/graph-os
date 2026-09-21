"""The WebUI co-service submits to FastMCP's one served event loop."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from graph_os.fleet.multiplexer import (
    MCPMultiplexer,
    SessionVisibilityMiddleware,
    attach_fleet_loader,
)
from graph_os.fleet.shared_multiplexer import (
    ServedMultiplexerBindingError,
    _reset_served_multiplexer_for_tests,
    bind_served_multiplexer,
    claim_served_multiplexer_loop,
    get_served_multiplexer,
    run_on_served_multiplexer,
)
from graph_os.webui_host.mcp_delegation import webui_mcp_delegation_helpers


@pytest.fixture(autouse=True)
def reset_binding() -> Iterator[None]:
    _reset_served_multiplexer_for_tests()
    yield
    _reset_served_multiplexer_for_tests()


class _LoopOwner:
    def __init__(self, mux: object):
        self.mux = cast(MCPMultiplexer, mux)
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        bind_served_multiplexer(self.mux)
        self.loop.call_soon(self._claim)
        self.loop.run_forever()
        self.loop.close()

    def _claim(self) -> None:
        claim_served_multiplexer_loop(self.mux)
        self.ready.set()

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(timeout=2)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


def test_cross_loop_submission_preserves_identity_context_and_object() -> None:
    mux = object()
    owner = _LoopOwner(mux)
    actor = contextvars.ContextVar[str]("actor", default="missing")
    owner.start()
    try:

        async def caller() -> tuple[bool, str, int]:
            actor.set("webui-user")

            async def inspect(served: MCPMultiplexer) -> tuple[bool, str, int]:
                return served is mux, actor.get(), threading.get_ident()

            return await run_on_served_multiplexer(inspect)

        same, observed_actor, thread_id = asyncio.run(caller())
        assert same is True
        assert observed_actor == "webui-user"
        assert thread_id == owner.thread.ident
    finally:
        owner.close()


def test_raw_authority_fails_closed_off_owner_loop() -> None:
    owner = _LoopOwner(object())
    owner.start()
    try:
        with pytest.raises(ServedMultiplexerBindingError, match="owner loop"):
            asyncio.run(get_served_multiplexer())
    finally:
        owner.close()


@pytest.mark.asyncio
async def test_attached_serving_middleware_claims_loop_for_same_instance(
    tmp_path: Any,
) -> None:
    from fastmcp import FastMCP

    config = tmp_path / "mcp.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")
    host = FastMCP("owner-loop-proof")
    mux = attach_fleet_loader(host, config_path=str(config))
    middleware = SessionVisibilityMiddleware(mux, host)

    async def call_next(_context: object) -> list[object]:
        return []

    assert await middleware.on_list_tools(SimpleNamespace(), call_next) == []

    async def identity(served: MCPMultiplexer) -> bool:
        return served is mux

    assert await run_on_served_multiplexer(identity) is True


def test_webui_helpers_use_one_owner_loop_for_inventory_call_and_resource() -> None:
    class FakeMultiplexer:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, Any]] = []

        async def delegated_server_tools(self, server: str) -> list[dict[str, Any]]:
            self.calls.append(("list", threading.get_ident(), server))
            return [{"name": "search"}]

        async def delegate_server_tool(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("call", threading.get_ident(), kwargs))
            return {"ok": True}

        async def read_server_resource(self, **kwargs: Any) -> dict[str, str]:
            self.calls.append(("read", threading.get_ident(), kwargs))
            return {"uri": kwargs["uri"], "text": "<main />", "mimeType": "text/html"}

    mux = FakeMultiplexer()
    owner = _LoopOwner(mux)
    owner.start()
    try:
        helpers = webui_mcp_delegation_helpers()

        async def exercise() -> tuple[Any, Any, Any]:
            tools = await helpers["list_mcp_server_tools"](server_name="docs")
            result = await helpers["call_mcp_tool"](
                server_name="docs", tool_name="search", arguments={"q": "x"}
            )
            resource = await helpers["read_mcp_resource"](
                server_name="docs", uri="ui://docs/main"
            )
            return tools, result, resource

        tools, result, resource = asyncio.run(exercise())
        assert tools == [{"name": "search"}]
        assert result == {"ok": True}
        assert resource["text"] == "<main />"
        assert [call[0] for call in mux.calls] == ["list", "call", "read"]
        assert {call[1] for call in mux.calls} == {owner.thread.ident}
    finally:
        owner.close()


@pytest.mark.asyncio
async def test_native_delegation_uses_served_probe_child_pool_and_bounds(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")
    mux = MCPMultiplexer(config)
    monkeypatch.setattr(
        "graph_os.fleet.multiplexer._require_fleet_capability", lambda *_args: None
    )
    mux.probe_server = AsyncMock(  # type: ignore[method-assign]
        return_value={"tools": [{"name": "search", "inputSchema": {}}], "error": None}
    )
    mux.mount_child = AsyncMock(return_value=[])  # type: ignore[method-assign]
    mux.tool_to_server["docs__search"] = ("docs", "search")
    mux.call_proxied_tool = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            structured_content={"result": {"hits": 2}}, content=[]
        )
    )
    resource_session = SimpleNamespace(
        read_resource=AsyncMock(
            return_value=SimpleNamespace(
                contents=[SimpleNamespace(text="<main />", mime_type="text/html")]
            )
        )
    )
    mux.sessions["docs"] = cast(Any, resource_session)

    assert await mux.delegated_server_tools("docs") == [
        {"name": "search", "inputSchema": {}}
    ]
    assert await mux.delegate_server_tool(
        server_name="docs",
        tool_name="search",
        arguments={"q": "graph"},
        timeout=1.0,
    ) == {"hits": 2}
    assert await mux.read_server_resource(
        server_name="docs", uri="ui://docs/main", timeout=1.0
    ) == {
        "uri": "ui://docs/main",
        "text": "<main />",
        "mimeType": "text/html",
    }
    mux.mount_child.assert_awaited()
    mux.call_proxied_tool.assert_awaited_once_with("docs__search", {"q": "graph"})
