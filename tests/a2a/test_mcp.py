"""A2A keeps native protocol routes after the intent-tool cutover."""

from __future__ import annotations

from typing import Any

import pytest

from graph_os.a2a import mcp as a2a_mcp
from graph_os.mcp_server import runtime


class _Mcp:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, tuple[str, ...]], Any] = {}

    def custom_route(self, path: str, *, methods: list[str]) -> Any:
        def decorate(function: Any) -> Any:
            self.routes[(path, tuple(methods))] = function
            return function

        return decorate


def test_registration_keeps_native_protocol_without_granular_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = object()
    rpc = object()
    service = object()
    monkeypatch.setattr(a2a_mcp, "_service", lambda: service)
    monkeypatch.setattr(
        a2a_mcp,
        "create_a2a_handlers",
        lambda *, service, authenticator: (card, rpc),
    )
    mcp = _Mcp()
    prior = runtime.REGISTERED_TOOLS.get("graph_a2a")
    a2a_mcp.register_a2a_protocol_routes(mcp)
    assert mcp.routes == {
        ("/.well-known/agent-card.json", ("GET",)): card,
        ("/a2a", ("POST",)): rpc,
    }
    assert runtime.REGISTERED_TOOLS.get("graph_a2a") is prior
    from graph_os.api.ops.agents import operations

    assert "agents.tasks.get" in {op.id for op in operations()}
