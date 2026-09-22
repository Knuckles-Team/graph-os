"""Focused wiring checks for the native graph-os fleet composition."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass

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
from graph_os.fleet.session_notifications import SessionCatalogNotifications

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


def test_reader_is_the_only_catalog_authority() -> None:
    snapshot = FleetCatalog(
        context=CONTEXT,
        servers=(_server("remote-child", "https://engine.example/mcp", {}),),
    )
    mux = MCPMultiplexer(Reader(snapshot))

    assert mux.load_catalog() == {}
    catalog = asyncio.run(mux.refresh_engine_catalog())

    assert set(catalog) == {"remote-child"}
    assert catalog["remote-child"]["url"] == "https://engine.example/mcp"
    assert catalog["remote-child"]["transport"] == "streamable-http"
    assert "config_path" not in inspect.signature(MCPMultiplexer).parameters
    assert "MCP_CONFIG" not in inspect.getsource(MCPMultiplexer)


def test_engine_component_transport_policy_is_preserved() -> None:
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
    mux = MCPMultiplexer(Reader(snapshot))

    catalog = asyncio.run(mux.refresh_engine_catalog())

    assert catalog["oauth-child"]["url"] == "https://engine.example/oauth"
    assert catalog["oauth-child"]["transport"] == "sse"
    assert catalog["oauth-child"]["oauth_provider"] == {"provider_id": "provider-a"}
    assert catalog["oauth-child"]["timeout"] == 15


def test_retired_reconciliation_symbols_are_absent() -> None:
    import graph_os.fleet.multiplexer as module

    source = inspect.getsource(module)
    for retired in (
        'name="catalog_refresh"',
        'name="catalog_dispatch"',
        'name="catalog_session_resume"',
        "McpCatalogReconciler",
        "_fleet_catalog_writer",
    ):
        assert retired not in source


def test_session_notification_state_is_ephemeral_and_acknowledged() -> None:
    notifications = SessionCatalogNotifications()

    notifications.queue(["session-a", "session-b"])
    generation = notifications.pending_generation("session-a")

    assert generation == 1
    assert notifications.pending_generation("session-b") == generation
    notifications.acknowledge("session-a", generation)
    assert not notifications.has_pending("session-a")
    assert notifications.has_pending("session-b")
    notifications.clear()
    assert not notifications.has_pending("session-b")
