"""Test-only fleet declarations for transport and lifecycle unit tests.

Production GraphOS has no JSON catalog path.  These helpers keep child-runtime
tests small by installing explicit in-memory declarations after constructing a
multiplexer with a reader that must never be called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from graph_os.fleet.multiplexer import MCPMultiplexer, attach_fleet_loader


class _NeverRead:
    async def read(self) -> Any:
        raise AssertionError("this unit test did not compose an EG catalog")


def _install(mux: MCPMultiplexer, path: Path) -> MCPMultiplexer:
    if path.is_symlink():
        mux._catalog = {}
        return mux
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        mux._catalog = {}
        return mux
    mux._catalog = {}
    for name, config in servers.items():
        if not mux._catalog_entry_admissible(
            name, config, {"mcp-multiplexer", "graph-os"}
        ):
            continue
        admitted = mux._admit_catalog_entry(name, config, False)
        if admitted is not None:
            mux._catalog[name] = admitted
    return mux


def multiplexer_from_fixture(path: Path) -> MCPMultiplexer:
    return _install(MCPMultiplexer(_NeverRead()), path)


def attach_multiplexer_from_fixture(host: Any, path: Path) -> MCPMultiplexer:
    return _install(attach_fleet_loader(host, catalog_reader=_NeverRead()), path)
