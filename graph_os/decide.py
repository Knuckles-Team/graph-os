"""graph-os boot composition of AU's Decide consumers (lane decide-consumers contract).

At startup, once the engine and the process's verified session exist,
graph-os installs:

* AU's :class:`~agent_utilities.decide.DecisionRunner` for the process tenant,
  over EG's generated ``Decide``/``DecisionLog`` senders
  (:class:`~agent_utilities.decide.transport.GeneratedTransport`). Sync call
  sites drive it on a dedicated decision loop that graph-os owns.
* AU's :class:`~agent_utilities.decide.consumers.assembly.Assembler` (EG
  ``AgentAssemble``) with the ``DecisionCommit`` mutation-context provider
  that AU deliberately never mints itself.

Point bindings are resolved from the Agent Library: each point's
``decide.schema.<question_id>`` FeatureSchema component (and an optional
promoted ``decide.head.<question_id>`` DecisionHead) at its current
revision. An unpublished point stays unbound and runs its deterministic
fallback, so installing the runner changes no behaviour until an operator
publishes a schema. graph-os consumes no Agent Library publish event, so
:class:`RefreshingBindings` re-reads the bindings on a bounded interval: a
newly published schema binds within one interval, and a retired one returns
its point to the fallback. A failed re-read keeps the last good snapshot
(logged); an unreachable EG makes every decision fall back anyway.

Mutation contexts (``DecisionCommit``, routed agent/graph publishes) are
minted by :mod:`graph_os.decide_context`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from graph_os.decide_context import (
    DecideCommitRefused,
    MutationAuthority,
    decision_commit_context,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DecideCommitRefused",
    "DecideComposition",
    "DecideLoop",
    "RefreshingBindings",
    "current_decide",
    "decision_commit_context",
    "install_decide",
    "install_decide_at_boot",
    "resolve_bindings",
]

_SYNC_TIMEOUT_S = 10.0
#: Default, minimum and maximum binding re-read interval (seconds).
REFRESH_INTERVAL_S = 60.0
_REFRESH_BOUNDS_S = (5.0, 3600.0)


class DecideLoop:
    """A daemon event loop graph-os owns for sync decision call sites."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self.loop.run_forever, name="graph-os-decide", daemon=True
        )
        self._thread.start()

    def run(self, call: Awaitable[Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(_awaited(call), self.loop)
        return future.result(timeout=_SYNC_TIMEOUT_S)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=_SYNC_TIMEOUT_S)


async def _awaited(call: Awaitable[Any]) -> Any:
    return await call


def _dependency(entry: Any) -> dict[str, Any]:
    kind = getattr(entry.kind, "value", entry.kind)
    return {
        "component_id": str(entry.component_id),
        "kind": str(kind),
        "definition_digest": str(entry.definition_digest),
    }


async def _current(components: Any, tenant: str, component_id: str) -> Any:
    from epistemic_graph.generated.agent_component import AgentComponentOpCurrent

    return await components.current(
        AgentComponentOpCurrent(
            op="current", component_id=component_id, tenant_id=tenant
        )
    )


async def resolve_bindings(components: Any, tenant: str) -> Any:
    """``StaticBindings`` for every point whose FeatureSchema is published."""
    from agent_utilities.decide import POINTS, StaticBindings
    from agent_utilities.decide.points import Binding

    bound: dict[str, Any] = {}
    for question_id, point in POINTS.items():
        schema = await _current(components, tenant, point.schema_component_id)
        if schema is None:
            continue
        head = await _current(components, tenant, f"decide.head.{question_id}")
        bound[question_id] = Binding(
            feature_schema=_dependency(schema),
            policy={"policy": "default"},
            head=None if head is None else _dependency(head),
        )
    return StaticBindings(bound)


class RefreshingBindings:
    """AU ``Bindings`` over an atomically swapped snapshot, re-read on an interval."""

    def __init__(self, components: Any, tenant: str, initial: Any) -> None:
        self._components = components
        self._tenant = tenant
        self._snapshot = initial

    @property
    def by_question(self) -> dict[str, Any]:
        return dict(self._snapshot.by_question)

    def binding_for(self, point: Any) -> Any:
        return self._snapshot.binding_for(point)

    async def refresh(self) -> bool:
        """Re-read every binding; ``False`` (snapshot kept) when the read fails."""
        try:
            fresh = await resolve_bindings(self._components, self._tenant)
        except (RuntimeError, ConnectionError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Decide binding refresh failed (%s); keeping the last snapshot",
                type(exc).__name__,
            )
            return False
        if set(fresh.by_question) != set(self._snapshot.by_question):
            logger.info(
                "Decide bindings changed: %d points bound", len(fresh.by_question)
            )
        self._snapshot = fresh
        return True

    async def refresh_forever(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            await self.refresh()


def _bounded_interval(interval_s: float) -> float:
    low, high = _REFRESH_BOUNDS_S
    if not low <= interval_s <= high:
        raise ValueError(f"binding refresh interval must be within {low}..{high}s")
    return interval_s


@dataclass(frozen=True)
class DecideComposition:
    """What boot installed: the runner, the assembler and their loop."""

    runner: Any
    assembler: Any
    loop: DecideLoop
    bindings: RefreshingBindings
    refresher: Any
    authority: MutationAuthority
    #: The swarm-topology catalog (templates + capacity scope), when installed.
    topology: Any = None

    def uninstall(self) -> None:
        from agent_utilities import decide
        from agent_utilities.decide.consumers.assembly import install_assembler
        from agent_utilities.decide.consumers.topology import install_topology

        self.refresher.cancel()
        if self.topology is not None and self.topology.refresher is not None:
            self.topology.refresher.cancel()
        decide.install_runner(None)
        install_assembler(None)
        install_topology(None)
        _INSTALLED[0] = None
        self.loop.stop()


_INSTALLED: list[DecideComposition | None] = [None]


def current_decide() -> DecideComposition | None:
    """The installed composition, or ``None`` (every call site falls back)."""
    return _INSTALLED[0]


def _topology_catalog() -> Any:
    from graph_os.decide_topology import TopologyCatalog

    return TopologyCatalog()


def _install_topology(composition: DecideComposition, interval_s: float) -> None:
    """Bind AU's swarm-topology asker (ST-9); a failure keeps topology
    questions unanswered (they abstain upward), never the rest of Decide."""
    from graph_os.decide_topology import install_topology_asker

    try:
        install_topology_asker(composition, composition.topology, interval_s)
    except (RuntimeError, TimeoutError, ValueError) as exc:
        logger.warning("topology asker not installed (%s)", type(exc).__name__)


def install_decide(
    client: Any,
    session: Any,
    policy: Any,
    refresh_interval_s: float = REFRESH_INTERVAL_S,
) -> DecideComposition:
    """Install the process runner and assembler for ``session``'s tenant."""
    from agent_utilities import decide
    from agent_utilities.decide.consumers.assembly import Assembler, install_assembler
    from agent_utilities.decide.transport import GeneratedTransport
    from agent_utilities.layers.clients import LayerClients

    tenant = str(session.tenant)
    interval = _bounded_interval(refresh_interval_s)
    loop = DecideLoop()
    layers = LayerClients.for_session(client, session)
    bindings = RefreshingBindings(
        layers.components,
        tenant,
        loop.run(resolve_bindings(layers.components, tenant)),
    )
    runner = decide.DecisionRunner(
        transport=GeneratedTransport(client=client, graph=tenant, loop=loop.loop),
        bindings=bindings,
        tenant=tenant,
    )
    authority = MutationAuthority(session, policy)
    assembler = Assembler(
        layers.graphs, tenant, commit_context=decision_commit_context(session, policy)
    )
    decide.install_runner(runner)
    install_assembler(assembler, run=loop.run)
    refresher = asyncio.run_coroutine_threadsafe(
        bindings.refresh_forever(interval), loop.loop
    )
    composition = DecideComposition(
        runner, assembler, loop, bindings, refresher, authority, _topology_catalog()
    )
    _install_topology(composition, interval)
    _INSTALLED[0] = composition
    logger.info(
        "Decide installed for tenant: %d of %d points bound",
        len(bindings.by_question),
        len(decide.POINTS),
    )
    return composition


def install_decide_at_boot(
    graph_client_for: Callable[[str], Any], session: Any, engine: Any
) -> DecideComposition | None:
    """Boot hook: install Decide, or keep every point on its fallback (logged)."""
    try:
        from agent_utilities.orchestration.action_policy import get_action_policy

        return install_decide(
            graph_client_for(str(session.tenant)), session, get_action_policy(engine)
        )
    except (ImportError, RuntimeError, TimeoutError) as exc:
        logger.warning(
            "Decide not installed (%s: %s); every decision point runs its "
            "deterministic fallback",
            type(exc).__name__,
            exc,
        )
        return None
