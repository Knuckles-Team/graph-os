"""Generated epistemic-graph fleet-catalog adapter contract."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
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
            # EG returns Digest256 as bare hex.
            registry_digest="a" * 64,
        )


TENANT_GRAPH = "tenant__tenant_a____commons__"


class Client:
    def __init__(self, *, registry: Registry | None = None) -> None:
        del registry


class GraphBinder:
    """Record which graph each generated read was narrowed to."""

    def __init__(self) -> None:
        self.bound: list[str] = []
        self.active: str | None = None

    @contextlib.contextmanager
    def __call__(self, graph: str) -> Iterator[None]:
        self.bound.append(graph)
        self.active = graph
        try:
            yield
        finally:
            self.active = None


def make_port(
    *, binder: GraphBinder | None = None, registry: Registry | None = None
) -> adapter.GeneratedFleetCatalogPort:
    return adapter.GeneratedFleetCatalogPort(
        tenant_client=Client(),
        commons_client=Client(registry=registry),
        context=CONTEXT,
        tenant_graph=TENANT_GRAPH,
        bind_graph=binder or GraphBinder(),
    )


@pytest.mark.asyncio
async def test_server_registry_is_read_through_the_commons_view_with_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry()
    binder = GraphBinder()
    port = make_port(binder=binder, registry=registry)
    sent: list[tuple[Any, Any, str | None, str | None]] = []

    async def list_servers(client: Any, params: Any, graph: str | None = None) -> Any:
        sent.append((client, params, graph, binder.active))
        return await registry.page(
            limit=params["request"]["limit"], cursor=params["request"].get("cursor")
        )

    monkeypatch.setattr(adapter, "send_list_registered_servers", list_servers)

    page = await port.query_registered_servers(COMMONS_GRAPH, 128, None)

    assert binder.bound == [COMMONS_GRAPH]
    # The commons view itself is the transport, targeted and bound to commons;
    # its base client's `server_registry` namespace is never used.
    assert sent == [
        (port._commons, {"request": {"limit": 128}}, COMMONS_GRAPH, COMMONS_GRAPH)
    ]
    assert registry.calls == [(128, None)]
    assert page.total_live == 1
    assert page.registry_revision == 7
    assert page.registry_digest == "sha256:" + "a" * 64
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

    binder = GraphBinder()

    async def search(client: Any, request: Any, graph: str | None) -> Any:
        calls.append(("search", request, graph))
        assert binder.active == graph
        return SimpleNamespace(entries=(server,), next_cursor=None)

    async def current(client: Any, request: Any, graph: str | None) -> Any:
        calls.append(("current", request, graph))
        assert binder.active == graph
        return server

    monkeypatch.setattr(adapter, "send_agent_component_search", search)
    monkeypatch.setattr(adapter, "send_agent_component_current", current)
    port = make_port(binder=binder)

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
    assert all(call[2] == TENANT_GRAPH for call in calls)
    assert binder.bound == [TENANT_GRAPH, TENANT_GRAPH]
    assert page.receipt.graph == CONTEXT.tenant_id


@pytest.mark.asyncio
async def test_adapter_rejects_cross_tenant_and_non_commons_reads() -> None:
    port = make_port()

    with pytest.raises(ValueError, match="__commons__"):
        await port.query_registered_servers("tenant-a", 10, None)
    with pytest.raises(ValueError, match="verified tenant"):
        await port.current_component(
            tenant_id="tenant-b", component_id="component:server"
        )
