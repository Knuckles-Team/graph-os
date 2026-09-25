"""MCPI-16 authority-bound SDK facade contracts."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.api.ops.ingest import specs
from graph_os.ingest import service as ingest


@dataclass(frozen=True)
class Descriptor:
    connector: str


class Registry:
    async def connectors(self) -> tuple[Descriptor, ...]:
        return (Descriptor("zeta"), Descriptor("alpha"))


class Sink:
    async def source_status(self, connector: str, stream: str) -> dict[str, str]:
        return {"connector": connector, "stream": stream}


class Runner:
    def __init__(self, registry: Any, services: Any) -> None:
        self.registry = registry

    async def run_once(self) -> dict[str, bool]:
        return {item.connector: True for item in await self.registry.connectors()}


@pytest.fixture
def sdk() -> ingest.IngestService:
    return ingest.IngestService(
        Registry(), SimpleNamespace(sink=Sink()), runner_factory=Runner
    )


def test_declared_ops_have_unique_ids_and_guarded_effects() -> None:
    items = specs()
    assert len(items) == 19
    assert len({item.id for item in items}) == len(items)
    approve = next(item for item in items if item.id == "ingest.drift.repair.approve")
    assert approve.confirm.value == "console"
    assert approve.principals.value == "human_undelegated"


@pytest.mark.asyncio
async def test_lists_only_connector_names_with_cursor(
    sdk: ingest.IngestService,
) -> None:
    assert await sdk.call("ingest.sources.list", {"limit": 1}) == {
        "items": ["alpha"],
        "next_cursor": "alpha",
    }
    assert await sdk.call("ingest.sources.list", {"limit": 1, "cursor": "alpha"}) == {
        "items": ["zeta"],
        "next_cursor": None,
    }


@pytest.mark.asyncio
async def test_status_and_sync_select_registered_connector(
    sdk: ingest.IngestService,
) -> None:
    assert await sdk.call(
        "ingest.sources.status", {"connector": "alpha", "stream": "one"}
    ) == {"connector": "alpha", "stream": "one"}
    assert await sdk.call("ingest.sources.sync", {"connector": "alpha"}) == {
        "connector": "alpha",
        "cycle_succeeded": True,
    }
    with pytest.raises(ingest.IngestUnavailable, match="not registered"):
        await sdk.call("ingest.sources.sync", {"connector": "unknown"})
    with pytest.raises(ingest.IngestUnavailable, match="per-stream"):
        await sdk.call("ingest.sources.sync", {"connector": "alpha", "stream": "one"})


@pytest.mark.asyncio
async def test_missing_control_port_and_runtime_binding_fail_closed(
    sdk: ingest.IngestService,
) -> None:
    with pytest.raises(ingest.IngestUnavailable, match="not bound"):
        await sdk.call("ingest.drift.get", {"report_id": "r1"})
    op = next(item for item in specs() if item.id == "ingest.sources.list")
    with pytest.raises(ingest.IngestUnavailable, match="not bound"):
        await ingest.execute(SimpleNamespace(services={}), {}, op)
