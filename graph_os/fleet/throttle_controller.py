"""Error-budget throttling of fleet children on EG ``CapacityCell`` s (EH-406).

Every :data:`~graph_os.fleet.error_budget.WINDOW_S` seconds, for each running
child (its declared ``error_budget``, or the observe-only
:data:`DEFAULT_POLICY` budget when it declares none):

1. the child's cell ``fleet/child/<name>`` is provisioned from the declaration
   (and redeclared when the operator changes it);
2. the window's requests and errors (live Prometheus facts) go to EG
   ``ThrottleCapacityCell``, which narrows the cell's ceiling on an error burst
   and gives it back only on recovery evidence, never above the declared
   capacity, and records every step on the cell and in the graph audit chain;
3. the ceiling EG returns is exported as
   ``agent_utilities_mcp_child_throttle_ceiling{server,mode}``. In ``observe``
   mode (the default, operator ruling 2026-09-24) that is all: the child's
   admission is never limited. Only a child that opts into ``enforce`` has its
   admission bounded by the ceiling
   (:meth:`~graph_os.fleet.child_resilience.ChildRuntime.apply_throttle_ceiling`).

Automatic actions only narrow: EG owns the AIMD arithmetic and the declared
bound, and a failed step keeps the last ceiling rather than widening. Every
:data:`EVOLVE_EVERY` windows, ENFORCED children whose declaration carries
``bounds`` also get one guardrail-profile evolution step (EH-407): tighten
within the ladder automatically, loosen only by human approval.

The limiter state lives in EG, so a restart resumes at the persisted ceiling.
The controller runs on the serving loop as a FastMCP server extension and
acts with GraphOS's own process identity, which holds exactly
``capacity:throttle`` (steps), ``capacity:admin`` (declare cells, apply evolved
profiles), ``capacity:lease`` and ``capacity:read`` (status) -- never
``kg:admin``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_utilities.observability.gateway_metrics import MCP_CHILD_THROTTLE_CEILING
from agent_utilities.security.guardrail_profile import (
    ErrorBudgetDeclaration,
    ThrottleMode,
)
from pydantic import ValidationError

from graph_os.fleet.error_budget import WINDOW_S, ChildWindow, prometheus_error_budget
from graph_os.mcp_server.background import BackgroundLoopExtension, process_authority

logger = logging.getLogger(__name__)

__all__ = [
    "CELL_PREFIX",
    "DECLARATION_KEY",
    "DEFAULT_POLICY",
    "EVOLVE_EVERY",
    "ThrottleController",
    "ThrottleControllerExtension",
    "ThrottleTarget",
    "attach_throttle_controller",
    "declared_targets",
    "default_declaration",
]

CELL_PREFIX = "fleet/child/"
#: The child configuration key holding its :class:`ErrorBudgetDeclaration`.
DECLARATION_KEY = "error_budget"
#: Windows between two guardrail-profile evolution steps (EH-407).
EVOLVE_EVERY = 15
#: The error budget every child gets when it declares none (operator ruling
#: 2026-09-24): observe-only -- computed, logged and exported, never enforced.
#: 5% errors narrow by half, <=1% recovers one slot per healthy minute.
DEFAULT_POLICY: dict[str, int] = {
    "error_budget_ppm": 50_000,
    "recovery_ppm": 10_000,
    "min_samples": 20,
    "decrease_per_mille": 500,
    "increase_step": 1,
    "floor": 1,
    "cooldown_ms": 60_000,
}
_APPLIED = frozenset({"accepted", "replayed"})

Authority = Callable[[], contextlib.AbstractContextManager[Any]]
EvolutionFactory = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class ThrottleTarget:
    """One running child and its declared error budget."""

    child: str
    runtime: Any
    declaration: ErrorBudgetDeclaration

    @property
    def cell_id(self) -> str:
        return CELL_PREFIX + self.child

    @property
    def declaration_digest(self) -> str:
        body = json.dumps(self.declaration.model_dump(mode="json"), sort_keys=True)
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def default_declaration(runtime: Any) -> ErrorBudgetDeclaration:
    """The observe-only budget a child gets without a (valid) declaration."""
    capacity = max(1, int(getattr(runtime, "max_concurrency", 1)))
    return ErrorBudgetDeclaration.model_validate(
        {"capacity": capacity, "policy": DEFAULT_POLICY, "mode": ThrottleMode.OBSERVE}
    )


def _declaration(name: str, runtime: Any) -> ErrorBudgetDeclaration:
    raw = getattr(runtime, "cfg", {}).get(DECLARATION_KEY)
    if raw is None:
        return default_declaration(runtime)
    try:
        return ErrorBudgetDeclaration.model_validate(raw)
    except ValidationError as exc:
        # A typo must be visible and must never enforce anything: log it and
        # fall back to the observe-only default.
        logger.error(
            "Child %s declares an invalid error_budget (%d problem(s)); "
            "it is observed with the default budget, never enforced",
            name,
            exc.error_count(),
        )
        return default_declaration(runtime)


def declared_targets(children: Mapping[str, Any]) -> list[ThrottleTarget]:
    """Every running child, with its declared or default (observe) budget."""
    return [
        ThrottleTarget(name, runtime, _declaration(name, runtime))
        for name, runtime in sorted(children.items())
    ]


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _declared_cell(
    target: ThrottleTarget, current: Mapping[str, Any] | None, now_ms: int
) -> dict[str, Any]:
    """The cell the declaration describes; a redeclaration keeps the narrowing."""
    capacity = target.declaration.capacity
    prior = (current or {}).get("throttle") or {}
    ceiling = min(int(prior.get("ceiling", capacity)), capacity)
    return {
        "cell_id": target.cell_id,
        "parent_id": None,
        "resource_class": "broker",
        "capacity": capacity,
        "reserved_floor": 0,
        "epoch": int((current or {}).get("epoch", 0)) + 1,
        "policy_digest": target.declaration_digest,
        "updated_at_ms": now_ms,
        "throttle": {
            "policy": target.declaration.policy.model_dump(),
            "ceiling": max(ceiling, target.declaration.policy.floor),
            "last_change_at_ms": int(prior.get("last_change_at_ms", 0)),
            "last_window_end_ms": int(prior.get("last_window_end_ms", 0)),
            "history": list(prior.get("history", [])),
        },
    }


def _apply(target: ThrottleTarget, ceiling: int) -> None:
    """Enforce the ceiling only for an opted-in child; observe only logs it.

    An observed child's limiter is never narrowed. If it was enforced before
    (the operator switched it back to observe), its old ceiling is lifted.
    """
    runtime = target.runtime
    if target.declaration.mode is ThrottleMode.ENFORCE:
        runtime.apply_throttle_ceiling(ceiling)
        return
    if getattr(runtime, "throttle_ceiling", None) is not None:
        runtime.apply_throttle_ceiling(None)
    if ceiling < target.declaration.capacity:
        logger.info(
            "Child %s would be throttled to %d of %d (observe only)",
            target.child,
            ceiling,
            target.declaration.capacity,
        )


class ThrottleController:
    """One error-budget step per window for every declared child."""

    def __init__(
        self,
        *,
        children: Callable[[], Mapping[str, Any]],
        source: Any,
        authority: Authority,
        tenant_ref: str,
        evolution: EvolutionFactory | None = None,
        clock_ms: Callable[[], int] = _clock_ms,
    ) -> None:
        self._children = children
        self._source = source
        self._authority = authority
        self._tenant = tenant_ref
        self._evolution = evolution
        self._clock_ms = clock_ms
        self._declared: dict[str, str] = {}
        self._windows = 0

    async def step(self) -> list[dict[str, Any]]:
        """Run one window; returns one report per declared child."""
        targets = declared_targets(self._children())
        if not targets:
            return []
        now_ms = self._clock_ms()
        observed = await self._source.windows([t.child for t in targets], now_ms)
        windows = {window.child: window for window in observed}
        with self._authority() as client:
            reports = [
                await self._step_target(client, target, windows.get(target.child))
                for target in targets
            ]
            self._windows += 1
            if self._evolution is not None and self._windows % EVOLVE_EVERY == 0:
                reports.extend(await _evolve(self._evolution(client), targets))
        return reports

    async def run(self) -> None:
        """Step once per window until cancelled; a failed window is logged."""
        while True:
            try:
                await self.step()
            except Exception as exc:
                logger.warning(
                    "Error-budget window failed (%s); ceilings unchanged",
                    type(exc).__name__,
                )
            await asyncio.sleep(WINDOW_S)

    async def _step_target(
        self, client: Any, target: ThrottleTarget, window: ChildWindow | None
    ) -> dict[str, Any]:
        try:
            await self._declare(client, target)
            if window is None:
                return {"child": target.child, "outcome": "unobserved"}
            return await self._throttle(client, target, window)
        except Exception as exc:
            # The last applied ceiling stands: a failed step never widens.
            logger.warning(
                "Error-budget step for child %s failed (%s); ceiling unchanged",
                target.child,
                type(exc).__name__,
            )
            return {"child": target.child, "outcome": "failed"}

    async def _throttle(
        self, client: Any, target: ThrottleTarget, window: ChildWindow
    ) -> dict[str, Any]:
        from epistemic_graph.generated.coordination import send_throttle_capacity_cell

        result = await send_throttle_capacity_cell(
            client,
            {
                "request": {
                    "schema_version": "1",
                    "cell_id": target.cell_id,
                    "sample": window.sample(),
                    "now_ms": window.window_end_ms,
                }
            },
        )
        cell, action = result.payload["cell"], result.payload["action"]
        ceiling = int(cell["throttle"]["ceiling"])
        mode = target.declaration.mode
        MCP_CHILD_THROTTLE_CEILING.labels(server=target.child, mode=mode.value).set(
            ceiling
        )
        _apply(target, ceiling)
        return {
            "child": target.child,
            "mode": mode.value,
            "outcome": action["action"],
            "reason": action["reason"],
            "ceiling": ceiling,
        }

    async def _cell(self, client: Any, cell_id: str) -> Mapping[str, Any] | None:
        answer = await client.capacity_leases.status(
            {
                "schema_version": "1",
                "tenant_ref": self._tenant,
                "cell_id": cell_id,
                "lease_id": None,
                "max_count": 1,
                "cursor": None,
            }
        )
        return next(
            (cell for cell in answer["cells"] if cell.get("cell_id") == cell_id), None
        )

    async def _declare(self, client: Any, target: ThrottleTarget) -> None:
        """Provision the cell, or redeclare it after the operator changed it."""
        digest = target.declaration_digest
        if self._declared.get(target.child) == digest:
            return
        current = await self._cell(client, target.cell_id)
        if current is None or current.get("policy_digest") != digest:
            now_ms = self._clock_ms()
            answer = await client.capacity_leases.update_cell(
                {
                    "schema_version": "1",
                    "cell": _declared_cell(target, current, now_ms),
                    "expected_epoch": None if current is None else current["epoch"],
                    "now_ms": now_ms,
                }
            )
            if answer["decision"] not in _APPLIED:
                raise RuntimeError(f"cell declaration refused: {answer['decision']}")
        self._declared[target.child] = digest


async def _evolve(
    evolution: Any, targets: list[ThrottleTarget]
) -> list[dict[str, Any]]:
    """One guardrail-profile evolution step for every child with declared bounds."""
    reports: list[dict[str, Any]] = []
    for target in targets:
        bounds = target.declaration.bounds
        if bounds is None or target.declaration.mode is not ThrottleMode.ENFORCE:
            continue
        try:
            result = await evolution.evolve(target.cell_id, bounds)
        except Exception as exc:
            logger.warning(
                "Guardrail evolution for child %s failed (%s)",
                target.child,
                type(exc).__name__,
            )
            continue
        reports.append({"child": target.child, "evolution": result.outcome.value})
    return reports


class ThrottleControllerExtension(BackgroundLoopExtension):
    """Run the controller on the serving loop for the server's lifetime."""

    identifier = "graph-os/error-budget-throttle"

    def __init__(self, controller: ThrottleController) -> None:
        super().__init__(controller.run)


def attach_throttle_controller(
    mcp: Any,
    multiplexer: Any,
    session: Any,
    *,
    client_for: Callable[[str], Any],
    engine_for: Callable[[], Any],
) -> ThrottleController | None:
    """Compose the controller over ``multiplexer``'s children, or ``None``.

    Without a configured Prometheus there are no error-budget facts, so no
    child is throttled (and none is narrowed on invented data).
    """
    source = prometheus_error_budget()
    if source is None:
        logger.info("Error-budget throttling is off: no Prometheus URL is configured")
        return None
    tenant = str(session.tenant)
    authority = process_authority(session, client_for)

    def evolution(client: Any) -> Any:
        from agent_utilities.api.runtime import get_action_policy
        from agent_utilities.security.guardrail_evolution import (
            ActionPolicyLoosenApprovals,
            EgProfileStore,
            GuardrailEvolution,
        )

        return GuardrailEvolution(
            EgProfileStore(client, tenant_ref=tenant),
            ActionPolicyLoosenApprovals(get_action_policy(engine_for())),
        )

    controller = ThrottleController(
        children=lambda: dict(multiplexer.children),
        source=source,
        authority=authority,
        tenant_ref=tenant,
        evolution=evolution,
    )
    mcp.add_extension(ThrottleControllerExtension(controller))
    return controller
