"""Fleet servers from the MCP config are registered and renewed before lapsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from graph_os.deployment.semantic_provisioning import ensure_server_registrations
from graph_os.fleet.registration import configured_fleet_endpoints

HOUR_MS = 3600 * 1000


def test_endpoints_are_the_http_servers_except_graph_os(tmp_path: Path) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "graph-os": {
                        "url": "http://graph-os/mcp",
                        "transport": "streamable-http",
                    },
                    "container-manager-mcp": {
                        "url": "http://cm/mcp",
                        "transport": "streamable-http",
                    },
                    "github-mcp": {"url": "http://gh/mcp", "type": "http"},
                    "local-tool": {"command": "uvx", "args": ["x"]},
                    "sse-only": {"url": "http://s/sse", "transport": "sse"},
                }
            }
        )
    )
    assert configured_fleet_endpoints(config) == {
        "container-manager-mcp": "http://cm/mcp",
        "github-mcp": "http://gh/mcp",
    }


def test_missing_config_registers_nothing(tmp_path: Path) -> None:
    assert configured_fleet_endpoints(tmp_path / "absent.json") == {}


class _Registry:
    def __init__(self, leases: dict[str, int]) -> None:
        self.leases = leases
        self.calls: list[tuple[str, Any]] = []

    async def _send(self, method: str, params: Any, _graph: Any, **kw: Any) -> Any:
        self.calls.append((method, kw.get("idempotency_key")))
        if method == "RegisterServer":
            return "registered"
        return {
            "schema_version": 1,
            "entries": [
                {
                    "name": name,
                    "url": "u",
                    "transport": "streamable_http",
                    "desired": "enabled",
                    "resources": {},
                    "ttl_secs": 86_400,
                    "registered_at_ms": 1,
                    "last_heartbeat_ms": 1,
                    "lease_expires_at_ms": expiry,
                }
                for name, expiry in self.leases.items()
            ],
            "next_cursor": None,
            "observed_at_ms": 1,
            "total_live": len(self.leases),
            "registry_revision": 1,
            "registry_digest": "1" * 64,
        }


async def test_registers_absent_and_renews_only_lapsing_servers() -> None:
    now = 100 * HOUR_MS
    registry = _Registry({"fresh": now + 20 * HOUR_MS, "lapsing": now + HOUR_MS})
    registered = await ensure_server_registrations(
        registry,
        {"fresh": "u", "lapsing": "u", "absent": "u"},
        renew_margin_ms=3 * HOUR_MS,
        now_ms=now,
    )
    assert registered == ("lapsing", "absent")
    keys = [key for method, key in registry.calls if method == "RegisterServer"]
    assert all(key.endswith(f":{now // (3 * HOUR_MS)}") for key in keys)


async def test_without_margin_any_live_registration_is_kept() -> None:
    registry = _Registry({"lapsing": 1})
    assert await ensure_server_registrations(registry, {"lapsing": "u"}) == ()
