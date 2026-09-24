"""graph-os boot composition of the swarm-topology asker (ST-9).

The topology question is EG's (SWARM-TOPOLOGY-DECIDE-DESIGN); graph-os owns
what it reads at the process level and every effect it has:

* :class:`TopologyCatalog` -- the published reference topology templates (by
  their current revision, as ``AgentGraphEntryRef`` pins) and the capacity
  scope (one tenant cell per resource class, from ``CapacityStatus``), both
  re-read with the Decide bindings; a failed re-read keeps the last snapshot.
* :func:`lease_book_for` -- the ``AcquireCapacity``/``ReleaseCapacity`` book
  owned by graph-os's own verified principal (the ledger checks the owner).
* :func:`install_topology_asker` -- AU's process topology asker bound to the
  installed assembler, graph-os's decision loop and the catalog.

No configuration is added: templates are the published ones, and cells are
the tenant's own capacity cells.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from graph_os.a2a.topology_routing import PlanLeaseBook, TopologyScope

logger = logging.getLogger(__name__)

__all__ = [
    "TopologyCatalog",
    "install_topology_asker",
    "lease_book_for",
    "template_ref",
]

_STATUS_PAGES = 16


def _payload(value: Any) -> Any:
    return getattr(value, "payload", value)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """A generated model or a plain mapping, as a mapping; ``None`` otherwise."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(mode="json")
    return value if isinstance(value, Mapping) else None


def template_ref(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The ``AgentGraphEntryRef`` pinning one published template revision."""
    return {
        "tenant_id": str(entry["tenant_id"]),
        "graph_id": str(entry["graph_id"]),
        "entry_revision": int(entry["entry_revision"]),
        "definition_digest": str(entry["definition_digest"]),
        "actor_scope": str(entry["actor_scope"]),
        "purpose_id": str(entry["purpose_id"]),
        "policy_digest": str(entry["policy_digest"]),
        "composed_work_ceiling": int(entry["composed_work_ceiling"]),
    }


def _scope_of(cells: list[Mapping[str, Any]]) -> TopologyScope:
    """One cell per resource class: the lowest cell id of each."""
    chosen: dict[str, str] = {}
    for cell in sorted(cells, key=lambda c: str(c["cell_id"])):
        chosen.setdefault(str(cell["resource_class"]), str(cell["cell_id"]))
    return TopologyScope(cells=tuple(sorted(chosen.values())))


class TopologyCatalog:
    """The published topology templates and the capacity scope, as snapshots."""

    def __init__(self, graph_ids: tuple[str, ...] | None = None) -> None:
        if graph_ids is None:
            from agent_utilities.decide.topology import REFERENCE_TEMPLATES

            graph_ids = tuple(spec.graph_id for spec in REFERENCE_TEMPLATES)
        self.graph_ids = graph_ids
        self._refs: tuple[dict[str, Any], ...] = ()
        self.scope = TopologyScope(cells=())
        #: The periodic re-read, once installed (cancelled on uninstall).
        self.refresher: Any = None

    def templates(self) -> tuple[dict[str, Any], ...]:
        return self._refs

    async def _current(self, graphs: Any, tenant: str, graph_id: str) -> Any:
        from epistemic_graph.generated.storage import send_agent_graph

        answer = await send_agent_graph(
            graphs.client,
            {"op": {"op": "current", "tenant_id": tenant, "graph_id": graph_id}},
            graphs.graph,
        )
        return _payload(answer)

    async def _cells(self, client: Any, tenant: str) -> list[Mapping[str, Any]]:
        cells: list[Mapping[str, Any]] = []
        cursor: str | None = None
        for _page in range(_STATUS_PAGES):
            answer = _payload(
                await client.capacity_leases.status(
                    {
                        "schema_version": "1",
                        "tenant_ref": tenant,
                        "cell_id": None,
                        "lease_id": None,
                        "max_count": 128,
                        "cursor": cursor,
                    }
                )
            )
            cells.extend(_as_mapping(cell) or {} for cell in answer["cells"])
            cursor = answer["next_cursor"]
            if cursor is None:
                break
        return cells

    async def refresh(self, graphs: Any, tenant: str) -> bool:
        """Re-read both snapshots; ``False`` (both kept) when a read fails."""
        try:
            entries = [await self._current(graphs, tenant, g) for g in self.graph_ids]
            cells = await self._cells(graphs.client, tenant)
        except (
            RuntimeError,
            ConnectionError,
            TimeoutError,
            ValueError,
            KeyError,
        ) as exc:
            logger.warning(
                "topology catalog refresh failed (%s); keeping the last snapshot",
                type(exc).__name__,
            )
            return False
        published = (_as_mapping(e) for e in entries)
        self._refs = tuple(template_ref(e) for e in published if e is not None)
        self.scope = _scope_of(cells)
        return True

    async def refresh_forever(
        self, graphs: Any, tenant: str, interval_s: float
    ) -> None:
        """Re-read on the Decide bindings' interval, for the process lifetime."""
        while True:
            await asyncio.sleep(interval_s)
            await self.refresh(graphs, tenant)


def lease_book_for(composition: Any) -> PlanLeaseBook:
    """The plan lease book owned by graph-os's verified principal."""
    graphs = composition.assembler.graphs
    return PlanLeaseBook(
        graphs.client,
        tenant_ref=str(composition.assembler.tenant),
        owner_digest=composition.authority.principal,
    )


def install_topology_asker(
    composition: Any, catalog: TopologyCatalog, interval_s: float = 60.0
) -> None:
    """Bind AU's process topology asker to the installed Decide composition."""
    from agent_utilities.decide.consumers.topology import install_topology

    graphs, tenant = composition.assembler.graphs, str(composition.assembler.tenant)
    composition.loop.run(catalog.refresh(graphs, tenant))
    install_topology(composition.assembler, composition.loop.run, catalog.templates)
    catalog.refresher = asyncio.run_coroutine_threadsafe(
        catalog.refresh_forever(graphs, tenant, interval_s), composition.loop.loop
    )
