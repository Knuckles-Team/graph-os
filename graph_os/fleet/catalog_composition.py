"""Bind the existing EG reader and multiplexer cache to fleet operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from graph_os.fleet.catalog_items import CatalogItem, FleetCatalog
from graph_os.fleet.catalog_sources import CombinedFleetSource, SdkRead
from graph_os.fleet.multiplexer_ops import (
    Invoke,
    Loadable,
    Mount,
    MultiplexerOps,
    NativeName,
    Notify,
    ReadItem,
)
from graph_os.fleet.session_loads import SessionLoads


class VerifiedReader(Protocol):
    async def read(self) -> Any: ...


class CachedProbe(Protocol):
    _probe_cache: Mapping[str, Mapping[str, Any]]

    def status_snapshot(self) -> Mapping[str, Any]: ...


async def _cached_protocol(mux: CachedProbe) -> Mapping[str, Mapping[str, Any]]:
    """Use completed probes only; discovery never starts a fleet sweep here."""
    return dict(mux._probe_cache)


def compose_multiplexer_ops(
    *,
    reader: VerifiedReader,
    mux: CachedProbe,
    sdk_entries: SdkRead,
    visible: Callable[[CatalogItem, Any], Awaitable[bool]],
    loadable: Loadable,
    mount: Mount,
    notify: Notify,
    invoke: Invoke,
    native_name: NativeName | None = None,
    read_item: ReadItem | None = None,
    cap: int = 64,
    idle_ttl_seconds: int = 3600,
) -> MultiplexerOps:
    """Build one operation service without claiming the MCP cutover is wired.

    ``reader`` is the process's already-bound ``DeferredFleetCatalogReader``.
    ``mux`` is the same process singleton used by the MCP server. EH-627 owns
    ``visible``/``loadable`` and EH-628 owns the native mount/notification
    hooks; supplying neither cannot silently widen authority.
    """

    async def live() -> Mapping[str, Mapping[str, Any]]:
        return await _cached_protocol(mux)

    source = CombinedFleetSource(verified=reader.read, live=live, sdk=sdk_entries)
    catalog = FleetCatalog((source,), visible)

    def health() -> Mapping[str, Any]:
        return mux.status_snapshot().get("children", {})

    return MultiplexerOps(
        catalog=catalog,
        sessions=SessionLoads(cap=cap, idle_ttl_seconds=idle_ttl_seconds),
        loadable=loadable,
        mount=mount,
        notify=notify,
        invoke=invoke,
        health=health,
        native_name=native_name,
        read_item=read_item,
    )
