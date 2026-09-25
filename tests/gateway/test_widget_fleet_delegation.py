"""Connector widgets use the served fleet, never connector Python packages."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from graph_os.fleet.multiplexer import MCPMultiplexer
from graph_os.fleet.shared_multiplexer import (
    _reset_served_multiplexer_for_tests,
    bind_served_multiplexer,
    claim_served_multiplexer_loop,
)
from graph_os.gateway.aggregator import Aggregator
from graph_os.gateway.models import ServiceConfig
from graph_os.gateway.registry import Registry
from graph_os.gateway.widgets.github import Widget as GitHubWidget
from graph_os.gateway.widgets.vector_db import Widget as VectorWidget


class _FakeFleet:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []

    async def delegated_server_tools(self, server_name: str) -> list[dict[str, Any]]:
        assert server_name == "github-agent"
        if not self.available:
            return []
        return [
            {
                "name": "github_repos",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "get"]},
                        "params_json": {"type": "string"},
                    },
                },
            }
        ]

    async def delegate_server_tool(
        self,
        *,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float,
    ) -> Any:
        self.calls.append((server_name, tool_name, arguments, timeout))
        return [{"name": "repo-one"}, {"name": "repo-two"}]


@pytest.fixture(autouse=True)
def _reset_served_fleet() -> Iterator[None]:
    _reset_served_multiplexer_for_tests()
    yield
    _reset_served_multiplexer_for_tests()


def _service() -> ServiceConfig:
    return ServiceConfig(id="github", name="GitHub", widget_type="github")


@pytest.mark.asyncio
async def test_aggregator_delegates_widget_to_the_served_multiplexer() -> None:
    fleet = _FakeFleet()
    served = cast(MCPMultiplexer, fleet)
    bind_served_multiplexer(served)
    claim_served_multiplexer_loop(served)
    registry = Registry()
    registry._widgets["github"] = GitHubWidget
    aggregator = Aggregator(registry=registry)

    result = await aggregator._fetch_one(_service())

    assert result.status == "ok"
    assert result.fields == {"repos": 2, "open_prs": 0, "open_issues": 0}
    assert fleet.calls == [
        (
            "github-agent",
            "github_repos",
            {"action": "list", "params_json": "{}"},
            30.0,
        )
    ]


@pytest.mark.asyncio
async def test_missing_catalog_tool_fails_cleanly_without_import_fallback() -> None:
    fleet = _FakeFleet(available=False)
    served = cast(MCPMultiplexer, fleet)
    bind_served_multiplexer(served)
    claim_served_multiplexer_loop(served)
    registry = Registry()
    registry._widgets["github"] = GitHubWidget
    aggregator = Aggregator(registry=registry)

    result = await aggregator._fetch_one(_service())

    assert result.status == "error"
    assert result.error
    assert fleet.calls == []


@pytest.mark.asyncio
async def test_vector_widget_uses_admitted_fleet_tool() -> None:
    class VectorFleet(_FakeFleet):
        async def delegated_server_tools(
            self, server_name: str
        ) -> list[dict[str, Any]]:
            assert server_name == "vector-mcp"
            return [
                {
                    "name": "vector_collection_management",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"action": {"type": "string"}},
                    },
                }
            ]

        async def delegate_server_tool(
            self,
            *,
            server_name: str,
            tool_name: str,
            arguments: dict[str, Any],
            timeout: float,
        ) -> Any:
            self.calls.append((server_name, tool_name, arguments, timeout))
            return {"collections": [{"name": "one"}, {"name": "two"}]}

    fleet = VectorFleet()
    served = cast(MCPMultiplexer, fleet)
    bind_served_multiplexer(served)
    claim_served_multiplexer_loop(served)
    registry = Registry()
    registry._widgets["vector_db"] = VectorWidget

    result = await Aggregator(registry=registry)._fetch_one(
        ServiceConfig(id="vector", name="Vector", widget_type="vector_db")
    )

    assert result.status == "ok"
    assert result.fields == {"collections": 2, "points": 0, "status": "Online"}
    assert fleet.calls == [
        (
            "vector-mcp",
            "vector_collection_management",
            {"action": "list_collections"},
            30.0,
        )
    ]
