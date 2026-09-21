"""Startup composition for generated EG and public AU catalog ports."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.fleet.catalog_reader import DeferredFleetCatalogReader
from graph_os.mcp_server import catalog_composition as composition


class GraphCompute:
    def __init__(self) -> None:
        self.graphs: list[str] = []

    def for_graph(self, graph: str) -> Any:
        self.graphs.append(graph)
        return SimpleNamespace(async_client=SimpleNamespace(graph=graph))


class Session:
    def engine_verified_context(self) -> dict[str, str]:
        return {
            "tenant": "tenant-a",
            "principal": "principal-a",
            "agent_id": "agent-a",
            "audience": "epistemic-graph",
            "policy_version": "policy-a",
        }


class Multiplexer:
    def __init__(self, *, fail: bool = False) -> None:
        self.refreshes = 0
        self.fail = fail

    async def refresh_engine_catalog(self) -> None:
        self.refreshes += 1
        if self.fail:
            raise RuntimeError("catalog unavailable")


@pytest.mark.asyncio
async def test_startup_injects_generated_clients_and_public_catalog_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compute = GraphCompute()
    engine = SimpleNamespace(graph_compute=compute)
    deferred = DeferredFleetCatalogReader()
    mux = Multiplexer()
    workflows = object()
    agents = object()
    installed: list[Any] = []

    monkeypatch.setattr(
        composition,
        "configure_enhanced_catalog",
        lambda authority: installed.append(authority),
    )
    verified: list[dict[str, Any]] = []

    async def verify(**kwargs: Any) -> None:
        verified.append(kwargs)

    monkeypatch.setattr(composition, "verify_semantic_content", verify)
    monkeypatch.setattr(
        composition,
        "required_content_connectors",
        lambda: ("graph-os", "agent-utilities"),
    )

    reader = await composition.compose_catalog_authorities(
        engine=engine,
        session=Session(),
        deferred_fleet=deferred,
        multiplexer=mux,
        catalog_ports_factory=lambda received_engine, received_session: (
            workflows,
            agents,
        ),
    )

    assert compute.graphs == ["tenant-a", "__commons__"]
    assert mux.refreshes == 1
    assert verified == [
        {
            "client": SimpleNamespace(graph="tenant-a"),
            "tenant_id": "tenant-a",
            "graph": "tenant-a",
            "connectors": ("graph-os", "agent-utilities"),
        }
    ]
    assert installed[0]._fleet is reader
    assert installed[0]._workflows is workflows
    assert installed[0]._agents is agents
    assert deferred._reader is reader


@pytest.mark.asyncio
async def test_failed_initial_refresh_aborts_startup_without_static_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleNamespace(graph_compute=GraphCompute())
    deferred = DeferredFleetCatalogReader()
    monkeypatch.setattr(composition, "configure_enhanced_catalog", lambda value: None)

    async def verified(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(composition, "verify_semantic_content", verified)
    monkeypatch.setattr(
        composition, "required_content_connectors", lambda: ("graph-os",)
    )

    with pytest.raises(RuntimeError, match="catalog unavailable"):
        await composition.compose_catalog_authorities(
            engine=engine,
            session=Session(),
            deferred_fleet=deferred,
            multiplexer=Multiplexer(fail=True),
            catalog_ports_factory=lambda engine, session: (object(), object()),
        )

    # The EG reader is installed, but startup propagates the failed proof and
    # never reaches the serving loop or a static MCP_CONFIG declaration.
    assert deferred._reader is not None
