"""EH-604 capacity facade authority and fail-closed mode controls."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.api.ops.capacity import specs
from graph_os.fleet.throttle_service import (
    CapacityService,
    CapacityUnavailable,
    execute,
)
from graph_os.mcp_server import runtime


class Cells:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.cells: dict[str, dict[str, Any]] = {}

    async def status(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        cell = self.cells.get(request["cell_id"])
        return {"cells": [cell] if cell else [], "leases": [], "next_cursor": None}

    async def update_cell(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {"decision": "accepted", "cell": request["cell"]}


@pytest.fixture
def setup() -> tuple[CapacityService, Cells, dict[str, Any]]:
    cells = Cells()
    children = {"github": SimpleNamespace(cfg={}, max_concurrency=4)}
    target = SimpleNamespace(
        child="github",
        cell_id="fleet/child/github",
        declaration=SimpleNamespace(mode=SimpleNamespace(value="observe"), capacity=4),
    )
    return CapacityService(lambda: children, targets=lambda: [target]), cells, children


def test_registry_requires_distinct_read_and_admin_scopes() -> None:
    items = {item.id: item for item in specs()}
    assert len(items) == 4
    assert items["capacity.status"].scopes == {"capacity:read"}
    assert items["capacity.throttle.status"].scopes == {"capacity:read"}
    assert items["capacity.cells.update"].scopes == {"capacity:admin"}
    assert items["capacity.throttle.set_mode"].confirm.value == "console"


@pytest.mark.asyncio
async def test_status_binds_verified_tenant_and_update_uses_epoch(
    setup: tuple[CapacityService, Cells, dict[str, Any]],
) -> None:
    service, cells, _ = setup
    client = SimpleNamespace(capacity_leases=cells)
    context = SimpleNamespace(
        services={"capacity": service},
        client=client,
        caller=SimpleNamespace(tenant="tenant-a"),
    )
    status = next(item for item in specs() if item.id == "capacity.status")
    await execute(context, {"cell_id": "worker", "limit": 3}, status)
    assert cells.requests[-1] == {
        "schema_version": "1",
        "tenant_ref": "tenant-a",
        "cell_id": "worker",
        "lease_id": None,
        "max_count": 3,
        "cursor": None,
    }
    update = next(item for item in specs() if item.id == "capacity.cells.update")
    await execute(context, {"cell": {"cell_id": "worker"}, "expected_epoch": 7}, update)
    assert cells.requests[-1]["expected_epoch"] == 7
    assert cells.requests[-1]["cell"] == {"cell_id": "worker"}


@pytest.mark.asyncio
async def test_enforcement_requires_persisted_ceiling_and_config_writer(
    setup: tuple[CapacityService, Cells, dict[str, Any]],
) -> None:
    service, cells, children = setup
    client = SimpleNamespace(capacity_leases=cells)
    request = {"child": "github", "mode": "enforce"}
    with pytest.raises(CapacityUnavailable, match="configuration is not bound"):
        await service.call(
            "capacity.throttle.set_mode", request, client=client, tenant="t"
        )

    writes: list[tuple[str, str]] = []

    async def write_mode(child: str, mode: str) -> None:
        writes.append((child, mode))

    service = CapacityService(
        lambda: children, write_mode=write_mode, targets=service._targets
    )
    with pytest.raises(CapacityUnavailable, match="ceiling is unavailable"):
        await service.call(
            "capacity.throttle.set_mode", request, client=client, tenant="t"
        )
    assert writes == []
    cells.cells["fleet/child/github"] = {
        "cell_id": "fleet/child/github",
        "epoch": 2,
        "throttle": {"ceiling": 2},
    }
    result = await service.call(
        "capacity.throttle.set_mode", request, client=client, tenant="t"
    )
    assert result == {"child": "github", "mode": "enforce", "outcome": "updated"}
    assert writes == [("github", "enforce")]
    with pytest.raises(ValueError, match="must be observe or enforce"):
        await service.call(
            "capacity.throttle.set_mode",
            {"child": "github", "mode": "disabled"},
            client=client,
            tenant="t",
        )
    assert writes == [("github", "enforce")]
    with pytest.raises(ValueError, match="not mounted"):
        await service.call(
            "capacity.throttle.set_mode",
            {"child": "missing", "mode": "enforce"},
            client=client,
            tenant="t",
        )


@pytest.mark.asyncio
async def test_throttle_status_does_not_invent_unpersisted_ceiling(
    setup: tuple[CapacityService, Cells, dict[str, Any]],
) -> None:
    service, cells, _ = setup
    result = await service.call(
        "capacity.throttle.status",
        {},
        client=SimpleNamespace(capacity_leases=cells),
        tenant="t",
    )
    assert result["children"] == [
        {
            "child": "github",
            "cell_id": "fleet/child/github",
            "mode": "observe",
            "declared_capacity": 4,
            "ceiling": None,
            "epoch": None,
        }
    ]


def test_serving_binds_one_live_capacity_service(monkeypatch: Any) -> None:
    bindings: dict[str, Any] = {}
    projection = SimpleNamespace(
        services=SimpleNamespace(runtime=SimpleNamespace(bindings=bindings))
    )
    monkeypatch.setattr(runtime, "served_api", lambda: (projection, None))
    children: dict[str, Any] = {}

    runtime.bind_capacity_service(lambda: children)
    service = bindings["capacity"]
    assert isinstance(service, CapacityService)
    assert service._children() is children
    assert service._write_mode is None
    with pytest.raises(RuntimeError, match="cannot be bound twice"):
        runtime.bind_capacity_service(lambda: children)
