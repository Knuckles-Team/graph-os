"""Authenticated ingress to SDK runner and future ingestion control ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class IngestUnavailable(RuntimeError):
    """The authority-bound ingest service or requested backend is unavailable."""


@dataclass(frozen=True, slots=True)
class _SelectedRegistry:
    source: ConnectorRegistry
    connector: str

    async def connectors(self) -> tuple[Any, ...]:
        matches = tuple(
            item
            for item in await self.source.connectors()
            if item.connector == self.connector
        )
        if not matches:
            raise IngestUnavailable("connector is not registered")
        return matches


class ConnectorRegistry(Protocol):
    async def connectors(self) -> tuple[Any, ...]: ...


class RunnerServices(Protocol):
    sink: Any


class Runner(Protocol):
    async def run_once(self) -> dict[str, bool]: ...


def _sdk_runner(registry: ConnectorRegistry, services: RunnerServices) -> Runner:
    from agent_connector_sdk.runner.supervisor import ConnectorSyncRunner

    return ConnectorSyncRunner(registry, services)


class IngestControlPort(Protocol):
    """Control methods supplied by AUD-19a/27 and EG ingest contracts."""

    async def execute(self, operation: str, params: Mapping[str, Any]) -> Any: ...


class IngestService:
    """One explicitly composed, authority-bound SDK facade.

    The caller/invoke pipeline enforces scopes and confirmation before entry.
    GraphOS must inject this service from its verified composition root. No
    ambient client or local fallback is discovered here.
    """

    def __init__(
        self,
        registry: ConnectorRegistry,
        services: RunnerServices,
        *,
        controls: IngestControlPort | None = None,
        runner_factory: Callable[
            [ConnectorRegistry, RunnerServices], Runner
        ] = _sdk_runner,
    ) -> None:
        self._registry = registry
        self._services = services
        self._controls = controls
        self._runner_factory = runner_factory

    async def call(self, operation: str, params: Mapping[str, Any]) -> Any:
        if operation == "ingest.sources.list":
            return await self._list_sources(params)
        if operation == "ingest.sources.status":
            return await self._source_status(params)
        if operation == "ingest.sources.sync":
            return await self._sync_source(params)
        if self._controls is None:
            raise IngestUnavailable(f"{operation} control backend is not bound")
        return await self._controls.execute(operation, params)

    async def _list_sources(self, params: Mapping[str, Any]) -> dict[str, object]:
        items = sorted(item.connector for item in await self._registry.connectors())
        cursor = params.get("cursor")
        if cursor is not None and cursor not in items:
            raise ValueError("cursor does not name a registered connector")
        start = items.index(cursor) + 1 if cursor is not None else 0
        limit = int(params.get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        page = items[start : start + limit]
        return {
            "items": page,
            "next_cursor": page[-1] if start + limit < len(items) else None,
        }

    async def _source_status(self, params: Mapping[str, Any]) -> Any:
        connector = _required(params, "connector")
        stream = _required(params, "stream")
        await _SelectedRegistry(self._registry, connector).connectors()
        return await self._services.sink.source_status(connector, stream)

    async def _sync_source(self, params: Mapping[str, Any]) -> dict[str, object]:
        connector = _required(params, "connector")
        if params.get("stream") is not None:
            raise IngestUnavailable("SDK per-stream sync control is not available")
        selected = _SelectedRegistry(self._registry, connector)
        results = await self._runner_factory(selected, self._services).run_once()
        if not results.get(connector):
            raise IngestUnavailable("connector sync failed; no success receipt")
        return {"connector": connector, "cycle_succeeded": True}


def _required(params: Mapping[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


async def execute(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Composite handler; one invoke chokepoint, one injected SDK service."""
    service = context.services.get("ingest")
    if not isinstance(service, IngestService):
        raise IngestUnavailable("authenticated ingestion service is not bound")
    value = await service.call(op.id, params)
    return {"value": value}
