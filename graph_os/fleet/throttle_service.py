"""Authenticated capacity API facade over EG and the live fleet controller."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class CapacityUnavailable(RuntimeError):
    """A required capacity authority or persistent fleet config is absent."""


ModeWriter = Callable[[str, str], Awaitable[None]]


class CapacityService:
    """Thin facade; caller authority remains bound by the invoke pipeline.

    ``write_mode`` must atomically persist the child declaration and update
    the live runtime config on the serving loop. Without it, mode writes fail
    closed instead of silently reverting after a restart.
    """

    def __init__(
        self,
        children: Callable[[], Mapping[str, Any]],
        *,
        write_mode: ModeWriter | None = None,
        targets: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._children = children
        self._write_mode = write_mode
        self._targets = targets or self._declared_targets

    def _declared_targets(self) -> list[Any]:
        from graph_os.fleet.throttle_controller import declared_targets

        return declared_targets(self._children())

    async def call(
        self, operation: str, params: Mapping[str, Any], *, client: Any, tenant: str
    ) -> dict[str, Any]:
        if operation == "capacity.status":
            return await self._status(client, tenant, params)
        if operation == "capacity.cells.update":
            return await self._update_cell(client, params)
        if operation == "capacity.throttle.status":
            return await self._throttle_status(client, tenant, params)
        if operation == "capacity.throttle.set_mode":
            return await self._set_mode(client, tenant, params)
        raise CapacityUnavailable("capacity operation is not registered")

    async def _status(
        self, client: Any, tenant: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return await client.capacity_leases.status(
            {
                "schema_version": "1",
                "tenant_ref": tenant,
                "cell_id": params.get("cell_id"),
                "lease_id": None,
                "max_count": params.get("limit", 100),
                "cursor": params.get("cursor"),
            }
        )

    async def _update_cell(
        self, client: Any, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return await client.capacity_leases.update_cell(
            {
                "schema_version": "1",
                "cell": params["cell"],
                "expected_epoch": params.get("expected_epoch"),
                "now_ms": time.time_ns() // 1_000_000,
            }
        )

    async def _throttle_status(
        self, client: Any, tenant: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        targets = {item.child: item for item in self._targets()}
        requested = params.get("child")
        if requested is not None and requested not in targets:
            raise ValueError("child is not mounted")
        names = [requested] if requested is not None else sorted(targets)
        items = []
        for name in names:
            target = targets[name]
            answer = await self._status(client, tenant, {"cell_id": target.cell_id})
            cell = next(
                (
                    item
                    for item in answer["cells"]
                    if item.get("cell_id") == target.cell_id
                ),
                None,
            )
            throttle = cell.get("throttle", {}) if cell is not None else {}
            items.append(
                {
                    "child": name,
                    "cell_id": target.cell_id,
                    "mode": target.declaration.mode.value,
                    "declared_capacity": target.declaration.capacity,
                    "ceiling": throttle.get("ceiling"),
                    "epoch": cell.get("epoch") if cell is not None else None,
                }
            )
        return {"children": items}

    async def _set_mode(
        self, client: Any, tenant: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        child = str(params["child"])
        target = next(
            (item for item in self._targets() if item.child == child),
            None,
        )
        if target is None:
            raise ValueError("child is not mounted")
        if self._write_mode is None:
            raise CapacityUnavailable("persistent throttle configuration is not bound")
        mode = str(params["mode"])
        if mode not in {"observe", "enforce"}:
            raise ValueError("throttle mode must be observe or enforce")
        if mode == "enforce":
            answer = await self._status(client, tenant, {"cell_id": target.cell_id})
            cell = next(
                (
                    item
                    for item in answer["cells"]
                    if item.get("cell_id") == target.cell_id
                ),
                None,
            )
            if cell is None or not isinstance(
                cell.get("throttle", {}).get("ceiling"), int
            ):
                raise CapacityUnavailable("persisted throttle ceiling is unavailable")
        await self._write_mode(child, mode)
        return {"child": child, "mode": mode, "outcome": "updated"}


async def execute(context: Any, params: Mapping[str, Any], op: Any) -> dict[str, Any]:
    """Composite binding called only through the shared invoke pipeline."""
    service = context.services.get("capacity")
    if not isinstance(service, CapacityService):
        raise CapacityUnavailable("authenticated capacity service is not bound")
    value = await service.call(
        op.id, params, client=context.client, tenant=context.caller.tenant
    )
    return {"value": value}
