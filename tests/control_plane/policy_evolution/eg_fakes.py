"""In-process fakes of the EG seams the policy-evolution control half uses.

They reproduce the engine semantics the graph-os code relies on — capacity
cells with a floor ``background_ingestion`` may not spend, monotonic cell
epochs by CAS, fenced leases, and atomic node create-if-absent /
compare-and-set — so the tests exercise the real graph-os adapters
(``EgCapacityLeaseBook``, ``EgReleasePointerRepository``) at their seam.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any


class FakeCapacityLeases:
    def __init__(self) -> None:
        self.cells: dict[str, dict[str, Any]] = {}
        self.leases: dict[str, dict[str, Any]] = {}
        self._fences = itertools.count(1)
        self._idempotency: dict[str, str] = {}

    async def status(self, request: dict[str, Any]) -> dict[str, Any]:
        cell_id = request["cell_id"]
        cells = [self.cells[cell_id]] if cell_id in self.cells else []
        leases = [
            lease
            for lease in self.leases.values()
            if lease["cell_id"] == cell_id
            and request["lease_id"] in (None, lease["lease_id"])
        ]
        return {
            "schema_version": "1",
            "cells": cells,
            "leases": leases,
            "next_cursor": None,
        }

    async def update_cell(self, request: dict[str, Any]) -> dict[str, Any]:
        cell = dict(request["cell"])
        current = self.cells.get(cell["cell_id"])
        if request["expected_epoch"] != (current["epoch"] if current else None):
            return {
                "schema_version": "1",
                "decision": "stale_epoch",
                "cell": current,
                "message": None,
            }
        self.cells[cell["cell_id"]] = cell
        return {
            "schema_version": "1",
            "decision": "accepted",
            "cell": cell,
            "message": None,
        }

    def _leased(self, cell_id: str, now_ms: int) -> int:
        return sum(
            lease["amount"]
            for lease in self.leases.values()
            if lease["cell_id"] == cell_id
            and lease["state"] in ("active", "renewed")
            and lease["expires_at_ms"] > now_ms
        )

    async def acquire(self, request: dict[str, Any]) -> dict[str, Any]:
        replay = self._idempotency.get(request["idempotency_key"])
        if replay is not None and self.leases[replay]["state"] in ("active", "renewed"):
            return self._answer("replayed", [self.leases[replay]])
        demand = request["demands"][0]
        cell = self.cells[demand["cell_id"]]
        spare = cell["capacity"] - cell["reserved_floor"]
        if self._leased(cell["cell_id"], request["now_ms"]) + demand["amount"] > spare:
            return self._answer("exhausted", [])
        lease = {
            "lease_id": request["lease_id"],
            "cell_id": cell["cell_id"],
            "amount": demand["amount"],
            "lease_epoch": cell["epoch"],
            "fence_token": next(self._fences),
            "expires_at_ms": request["now_ms"] + request["ttl_ms"],
            "state": "active",
        }
        self.leases[lease["lease_id"]] = lease
        self._idempotency[request["idempotency_key"]] = lease["lease_id"]
        return self._answer("accepted", [lease])

    async def release(self, request: dict[str, Any]) -> dict[str, Any]:
        for fence in request["leases"]:
            lease = self.leases.get(fence["lease_id"])
            if lease is not None and lease["fence_token"] == fence["fence_token"]:
                lease["state"] = "released"
        return {
            "schema_version": "1",
            "decision": "released",
            "leases": [],
            "message": None,
        }

    def revoke(self, lease_id: str) -> None:
        self.leases[lease_id]["state"] = "expired"

    @staticmethod
    def _answer(decision: str, leases: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "decision": decision,
            "leases": leases,
            "available": [],
            "message": None,
        }


class FakeNodes:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    async def properties(self, node_id: str) -> dict[str, Any] | None:
        node = self.nodes.get(node_id)
        return None if node is None else dict(node)

    async def create_if_absent(self, node_id: str, properties: dict[str, Any]) -> bool:
        if node_id in self.nodes:
            return False
        self.nodes[node_id] = dict(properties)
        return True

    async def compare_and_set(
        self, node_id: str, conditions: dict[str, Any], updates: dict[str, Any]
    ) -> bool:
        node = self.nodes.get(node_id)
        if node is None or any(node.get(k) != v for k, v in conditions.items()):
            return False
        node.update(updates)
        return True


def fake_eg_client() -> Any:
    return SimpleNamespace(capacity_leases=FakeCapacityLeases(), nodes=FakeNodes())
