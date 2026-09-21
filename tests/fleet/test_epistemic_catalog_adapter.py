"""Generated epistemic-graph fleet-catalog adapter contract."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.fleet import epistemic_adapter as adapter
from graph_os.fleet.catalog_reader import (
    COMMONS_GRAPH,
    COMPONENT_CURRENT_SOURCE,
    COMPONENT_SEARCH_SOURCE,
    SERVER_QUERY_SOURCE,
    ComponentSearchRequest,
    ReadContext,
)

CONTEXT = ReadContext(
    tenant_id="tenant-a",
    principal_id="principal-a",
    agent_id="agent-a",
    audience="epistemic-graph",
    policy_version="policy-a",
)


class Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Any]] = []

    async def page(self, *, limit: int, cursor: Any) -> Any:
        self.calls.append((limit, cursor))
        return SimpleNamespace(
            entries=(
                SimpleNamespace(
                    name="github",
                    url="https://github.example/mcp",
                    resources={"health": "/health"},
                    registered_at_ms=10,
                    last_heartbeat_ms=11,
                    lease_expires_at_ms=100,
                ),
            ),
            next_cursor=SimpleNamespace(model_dump_json=lambda: '{"after":"github"}'),
            observed_at_ms=12,
            total_live=1,
            registry_revision=7,
            registry_digest="sha256:" + "a" * 64,
        )


class Client:
    def __init__(self, *, registry: Registry | None = None) -> None:
        self.server_registry = registry


@pytest.mark.asyncio
async def test_server_registry_facade_is_read_from_commons_with_exact_receipt() -> None:
    registry = Registry()
    port = adapter.GeneratedFleetCatalogPort(
        tenant_client=Client(),
        commons_client=Client(registry=registry),
        context=CONTEXT,
    )

    page = await port.query_registered_servers(COMMONS_GRAPH, 128, None)

    assert registry.calls == [(128, None)]
    assert page.total_live == 1
    assert page.registry_revision == 7
    assert page.entries[0].server_id == "srv:github"
    assert page.entries[0].resources == (("health", "/health"),)
    assert page.receipt.source == SERVER_QUERY_SOURCE
    assert page.receipt.graph == COMMONS_GRAPH


@pytest.mark.asyncio
async def test_component_reads_use_only_generated_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any, str | None]] = []
    server = SimpleNamespace(
        component_id="component:server",
        kind=SimpleNamespace(value="mcp_server"),
        summary="GitHub MCP",
        tenant_id="tenant-a",
        entry_revision=3,
        definition_digest="sha256:" + "b" * 64,
        content_digest="sha256:" + "c" * 64,
        lifecycle=SimpleNamespace(value="published"),
        attributes={"pack.connector": "github"},
        provenance=SimpleNamespace(),
    )

    async def search(client: Any, request: Any, graph: str | None) -> Any:
        calls.append(("search", request, graph))
        return SimpleNamespace(entries=(server,), next_cursor=None)

    async def current(client: Any, request: Any, graph: str | None) -> Any:
        calls.append(("current", request, graph))
        return server

    monkeypatch.setattr(adapter, "send_agent_component_search", search)
    monkeypatch.setattr(adapter, "send_agent_component_current", current)
    port = adapter.GeneratedFleetCatalogPort(
        tenant_client=Client(), commons_client=Client(), context=CONTEXT
    )

    page = await port.search_components(
        ComponentSearchRequest(tenant_id="tenant-a", kinds=("mcp_server",), limit=64)
    )
    resolved = await port.current_component(
        tenant_id="tenant-a", component_id="component:server"
    )

    assert page.entries[0].server_name == "github"
    assert page.receipt.source == COMPONENT_SEARCH_SOURCE
    assert resolved.entry == page.entries[0]
    assert resolved.receipt.source == COMPONENT_CURRENT_SOURCE
    assert [call[0] for call in calls] == ["search", "current"]
    assert all(call[2] == "tenant-a" for call in calls)


@pytest.mark.asyncio
async def test_adapter_rejects_cross_tenant_and_non_commons_reads() -> None:
    port = adapter.GeneratedFleetCatalogPort(
        tenant_client=Client(), commons_client=Client(), context=CONTEXT
    )

    with pytest.raises(ValueError, match="__commons__"):
        await port.query_registered_servers("tenant-a", 10, None)
    with pytest.raises(ValueError, match="verified tenant"):
        await port.current_component(
            tenant_id="tenant-b", component_id="component:server"
        )
