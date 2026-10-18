"""arbora registers itself as the agent harness's host at startup.

CONCEPT:ARBORA-HOST.harness.host-bindings

The harness (``agent_utilities``) never serves tools or parses the fleet
catalog. Its thin client host (``agent_utilities.host_ports``) fails
closed until arbora installs these bindings:

* a **native toolset provider** that binds a caller-granted subset of
  arbora's own served operations for one in-process delegation, dispatched
  through the governed ``_execute_tool`` path (no self-HTTP);
* a **fleet catalog provider** mapping each fleet server arbora serves to its
  public tool prefix and client endpoint, read from the exact multiplexer
  arbora serves; and
* a **remote toolset provider** and a **tool client factory**
  (:mod:`arbora.fleet.client_toolsets`): arbora's MCP client, carrying
  its outbound service credential; and
* an **authority keepalive** factory that renews the caller's renewable
  session lease for the whole of one delegation.

:func:`register_harness_host` installs them; :func:`unregister_harness_host`
clears them on shutdown so a stopped server never leaves a stale binding.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)


def _runtime() -> Any:
    from arbora.mcp_server import runtime

    return runtime


def _available_tools() -> tuple[str, ...]:
    return tuple(sorted(_runtime().REGISTERED_TOOLS))


def _build_toolset(tool_names: list[str], toolset_id: str) -> Any:
    return _runtime().build_native_arbora_toolset(
        list(tool_names), toolset_id=toolset_id
    )


def _keepalive() -> contextlib.AbstractAsyncContextManager[None]:
    return _runtime().authority_keepalive_scope()


def fleet_catalog_view(multiplexer: Any) -> Callable[[], Mapping[str, Any]]:
    """A provider over ``multiplexer``'s mountable catalog.

    Each entry carries the collision-free public prefix arbora serves the
    server under and its resolved (non-secret) client URL. A server whose
    entry cannot be rendered is left out rather than invented.
    """
    from agent_utilities.host_ports import FleetServer

    def view() -> dict[str, FleetServer]:
        from arbora.fleet.multiplexer import _resolve_runtime_value

        servers: dict[str, FleetServer] = {}
        for name, config in dict(multiplexer.load_catalog()).items():
            if not isinstance(name, str) or not name.strip():
                continue
            url = ""
            if isinstance(config, dict) and config.get("url"):
                try:
                    url = str(
                        _resolve_runtime_value(config.get("url", ""), sensitive=False)
                    )
                except Exception as exc:  # noqa: BLE001 - omit an unresolvable URL
                    logger.debug(
                        "fleet catalog URL unresolved for %s (%s)",
                        name,
                        type(exc).__name__,
                    )
            servers[name] = FleetServer(
                prefix=multiplexer.server_prefix(name), url=url.rstrip("/")
            )
        return servers

    return view


def register_harness_host(multiplexer: Any | None = None) -> None:
    """Install arbora's native, remote, client, catalog and keepalive bindings."""
    from agent_utilities import host_ports as host

    from arbora.fleet import client_toolsets

    host.register_native_toolset_provider(
        host.NativeToolsetProvider(
            available_tools=_available_tools, build_toolset=_build_toolset
        )
    )
    host.register_fleet_catalog_provider(
        fleet_catalog_view(multiplexer) if multiplexer is not None else None
    )
    host.register_remote_toolset_provider(client_toolsets.remote_toolset_provider())
    host.register_tool_client_factory(client_toolsets.open_tool_client)
    host.register_authority_keepalive(_keepalive)


def unregister_harness_host() -> None:
    """Clear every binding arbora installed."""
    from agent_utilities import host_ports as host

    host.register_native_toolset_provider(None)
    host.register_fleet_catalog_provider(None)
    host.register_remote_toolset_provider(None)
    host.register_tool_client_factory(None)
    host.register_authority_keepalive(None)


__all__ = [
    "fleet_catalog_view",
    "register_harness_host",
    "unregister_harness_host",
]
