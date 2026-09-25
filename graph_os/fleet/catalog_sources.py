"""Verified fleet, live protocol, and SDK entries as one catalog source."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from graph_os.fleet.catalog_items import (
    CatalogItem,
    connector_items,
    items_from_child_probe,
    items_from_eg_catalog,
)

VerifiedRead = Callable[[], Awaitable[Any]]
LiveRead = Callable[[], Awaitable[Mapping[str, Mapping[str, Any]]]]
SdkRead = Callable[[], Awaitable[Iterable[Mapping[str, Any]]]]


class CombinedFleetSource:
    """Only EG-admitted live servers may contribute probed protocol rows.

    EG owns component identity and body. A child probe may fill a tool schema
    or advertise a protocol family that EG does not model, but can never make
    an unregistered server visible. SDK entries arrive through a separate
    typed adapter; the caller supplies its verified pack reader.
    """

    def __init__(self, *, verified: VerifiedRead, live: LiveRead, sdk: SdkRead):
        self._verified = verified
        self._live = live
        self._sdk = sdk

    async def __call__(self) -> tuple[CatalogItem, ...]:
        snapshot = await self._verified()
        admitted = {
            server.component.server_name
            for server in snapshot.servers
            if server.registration is not None
        }
        merged = {
            item.id: item
            for item in items_from_eg_catalog(snapshot)
            if item.server in admitted
        }
        probe = await self._live()
        for server in admitted:
            for item in items_from_child_probe(server, probe.get(server, {})):
                previous = merged.get(item.id)
                if previous is None:
                    merged[item.id] = item
                elif item.kind == "tool":
                    merged[item.id] = replace(previous, schema=item.schema)
        for item in connector_items(await self._sdk()):
            if item.id in merged:
                raise ValueError(f"duplicate connector item: {item.id}")
            merged[item.id] = item
        return tuple(merged[name] for name in sorted(merged))
