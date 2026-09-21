"""Focused wiring checks for the native graph-os fleet composition."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from graph_os.fleet.catalog_reader import (
    CatalogServer,
    ComponentContent,
    ComponentRecord,
    FleetCatalog,
    ReadContext,
    ReadReceipt,
    ServerRegistration,
)
from graph_os.fleet.multiplexer import MCPMultiplexer
from graph_os.mcp_server.bootstrap import _bind_mcp_probe_port

CONTEXT = ReadContext(
    tenant_id="tenant-a",
    principal_id="principal-a",
    agent_id="agent-a",
    audience="graph-os",
    policy_version="policy:current",
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _server(name: str, url: str, body: dict[str, object]) -> CatalogServer:
    encoded = json.dumps(body, separators=(",", ":")).encode()
    server_id = f"srv:{name}"
    component_id = f"mcp:{name}"
    record = ComponentRecord(
        component_id=component_id,
        kind="mcp_server",
        server_name=name,
        upstream_name=name,
        summary=f"{name} server",
        tenant_id=CONTEXT.tenant_id,
        entry_revision=1,
        definition_digest=_digest(name),
        content_digest=_digest(encoded.decode()),
        lifecycle="published",
        provenance_server=None,
    )
    receipt = ReadReceipt(
        context=CONTEXT,
        source="AgentComponent.Content",
        graph=CONTEXT.tenant_id,
    )
    return CatalogServer(
        registration=ServerRegistration(
            server_id=server_id,
            name=name,
            url=url,
            resources=(("transport", "streamable-http"),),
            registered_at_ms=1,
            last_heartbeat_ms=2,
            lease_expires_at_ms=10_000,
        ),
        component=record,
        content=ComponentContent(
            component_id=component_id,
            entry_revision=1,
            definition_digest=record.definition_digest,
            content_digest=record.content_digest,
            media_type="application/json",
            body=encoded,
            receipt=receipt,
        ),
        provides=(),
    )


@dataclass
class Reader:
    catalog: FleetCatalog

    async def read(self) -> FleetCatalog:
        return self.catalog


def test_native_module_is_not_an_au_forwarding_facade() -> None:
    import graph_os.fleet.multiplexer as module

    assert module.__name__ == "graph_os.fleet.multiplexer"
    assert module.MCPMultiplexer.__module__ == "graph_os.fleet.multiplexer"


def test_engine_probe_port_uses_native_fleet_runtime() -> None:
    import graph_os.fleet.multiplexer as native

    engine = type("Engine", (), {})()

    _bind_mcp_probe_port(engine)

    assert engine.mcp_probe_port.multiplexer_factory is native.MCPMultiplexer
    assert engine.mcp_probe_port.resolve_config_path is native._resolve_config_path


def test_reader_authority_blocks_static_catalog_fallback(tmp_path: Path) -> None:
    static_path = tmp_path / "mcp_config.json"
    static_path.write_text(
        json.dumps({"mcpServers": {"static-child": {"url": "https://static"}}}),
        encoding="utf-8",
    )
    snapshot = FleetCatalog(
        context=CONTEXT,
        servers=(_server("remote-child", "https://engine.example/mcp", {}),),
    )
    mux = MCPMultiplexer(static_path, catalog_reader=Reader(snapshot))

    assert mux.load_catalog() == {}
    catalog = asyncio.run(mux.refresh_engine_catalog())

    assert set(catalog) == {"remote-child"}
    assert catalog["remote-child"]["url"] == "https://engine.example/mcp"
    assert catalog["remote-child"]["transport"] == "streamable-http"
    assert "static-child" not in catalog


def test_engine_component_transport_policy_is_preserved(tmp_path: Path) -> None:
    snapshot = FleetCatalog(
        context=CONTEXT,
        servers=(
            _server(
                "oauth-child",
                "https://engine.example/oauth",
                {
                    "config": {
                        "transport": "sse",
                        "oauth_provider": {"provider_id": "provider-a"},
                        "timeout": 15,
                    }
                },
            ),
        ),
    )
    mux = MCPMultiplexer(tmp_path / "missing.json", catalog_reader=Reader(snapshot))

    catalog = asyncio.run(mux.refresh_engine_catalog())

    assert catalog["oauth-child"]["url"] == "https://engine.example/oauth"
    assert catalog["oauth-child"]["transport"] == "sse"
    assert catalog["oauth-child"]["oauth_provider"] == {"provider_id": "provider-a"}
    assert catalog["oauth-child"]["timeout"] == 15
