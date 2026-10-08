"""Keep the configured fleet MCP servers registered in EG's server registry.

The fleet catalog reads ``RegisterServer`` rows in ``__commons__``. A fresh
store has none, and a registration lapses when its lease expires, so GraphOS
registers every streamable-HTTP server in the MCP config at boot and renews
each one before its lease lapses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How often the renewal thread re-checks the registry.
RENEW_INTERVAL_S = 3600.0
#: Renew a registration when its lease lapses within this window.
RENEW_MARGIN_MS = 3 * 3600 * 1000
_HTTP_TRANSPORTS = frozenset({"", "http", "streamable-http"})


def configured_fleet_endpoints(
    path: Path | None = None, *, self_name: str = "graph-os"
) -> dict[str, str]:
    """Return ``{server name: URL}`` for every streamable-HTTP server in the MCP config."""
    from agent_utilities.core.paths import mcp_config_path

    from graph_os.gateway.config import _parse_mcp_servers

    config = path or Path(os.environ.get("MCP_CONFIG") or mcp_config_path())
    if not config.is_file():
        return {}
    endpoints: dict[str, str] = {}
    for name, spec in _parse_mcp_servers(config.read_bytes()).items():
        if name == self_name or not isinstance(spec, dict):
            continue
        url = spec.get("url")
        transport = str(spec.get("transport") or spec.get("type") or "")
        if (
            isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and transport.replace("_", "-") in _HTTP_TRANSPORTS
        ):
            endpoints[str(name)] = url
    return endpoints


async def register_fleet(engine: Any, endpoints: dict[str, str]) -> tuple[str, ...]:
    """Register or renew each endpoint whose registration is absent or lapsing."""
    from graph_os.deployment.semantic_provisioning import (
        COMMONS_GRAPH,
        _bind_session_graph,
        ensure_server_registrations,
    )

    commons = engine.graph_compute.for_graph(COMMONS_GRAPH).async_client
    with _bind_session_graph(COMMONS_GRAPH):
        return await ensure_server_registrations(
            commons, endpoints, renew_margin_ms=RENEW_MARGIN_MS
        )


def _register_once(engine: Any, endpoints: dict[str, str]) -> None:
    try:
        registered = asyncio.run(register_fleet(engine, endpoints))
    except Exception as exc:  # a registry outage must not stop serving
        logger.warning("fleet registration failed; will retry: %s", exc)
        return
    if registered:
        logger.info("fleet servers registered or renewed: %s", ", ".join(registered))


def start_fleet_registration(
    engine: Any, session: Any, stop_event: threading.Event | None = None
) -> threading.Thread | None:
    """Register the fleet now, then renew it from a daemon thread."""
    if getattr(engine, "graph_compute", None) is None:
        return None
    endpoints = configured_fleet_endpoints()
    if not endpoints:
        return None
    _register_once(engine, endpoints)
    stop = stop_event or threading.Event()

    def renew() -> None:
        from agent_utilities.api.session import use_session
        from agent_utilities.security.brain_context import use_actor

        with use_actor(session.actor), use_session(session):
            while not stop.wait(RENEW_INTERVAL_S):
                _register_once(engine, endpoints)

    thread = threading.Thread(target=renew, name="fleet-registration", daemon=True)
    thread.start()
    return thread


__all__ = [
    "configured_fleet_endpoints",
    "register_fleet",
    "start_fleet_registration",
]
