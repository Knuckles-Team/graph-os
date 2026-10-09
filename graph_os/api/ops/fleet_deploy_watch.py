"""Deploy-watch verdict operation (GRAPHOS-FLEET-R024.1).

GraphOS-owned typed operation reproducing the deploy-watch verdict decision
that previously sat beside the agent runner in agent-utilities'
``orchestration/`` package (GRAPHOS-FLEET-R024). It runs behind the same
caller verification and policy filter as every other fleet operation: the
registry's :func:`graph_os.api.registry.authorized` chokepoint enforces the
declared ``scopes`` and the served policy decision before this handler is
ever reached, exactly as it does for :mod:`graph_os.api.ops.fleet`.

The durable watch-scheduling, observer-polling and rollback-dispatch pieces
of the original module (``watch_deploy``/``run_deploy_watch``/
``default_on_fail``) are GraphOS service-skeleton work for the sibling
reconciler/autoscaler/scaling-authority behaviors tracked under
GRAPHOS-FLEET-R024.2; this operation covers the verdict rule only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import Composite, Effect, OpSpec, Verb
from graph_os.control_plane.fleet_watch.service import (
    DeployProbe,
    evaluate_deploy_watch,
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FleetDeployWatchEvaluateParams(_Params):
    service: str = Field(min_length=1, max_length=256)
    probes: tuple[DeployProbe, ...] = ()


class FleetDeployWatchResult(BaseModel):
    outcome: str
    detail: str
    healthy_probes: int
    probes: int


async def handle_fleet_deploy_watch_evaluate(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Compute the deploy-watch verdict for a completed probe sequence."""

    del context, op  # authorization already ran at the registry chokepoint
    probes = tuple(
        probe if isinstance(probe, DeployProbe) else DeployProbe(**probe)
        for probe in params.get("probes", ())
    )
    verdict = evaluate_deploy_watch(params["service"], probes)
    return verdict.model_dump()


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="fleet.deploy_watch.evaluate",
            verb=Verb.ASK,
            summary=(
                "Evaluate a deploy-watch probe sequence's "
                "success/failed/unobserved verdict"
            ),
            examples=("did the deploy watch for this service pass",),
            params=FleetDeployWatchEvaluateParams,
            result=FleetDeployWatchResult,
            binding=Composite(
                handler=(
                    "graph_os.api.ops.fleet_deploy_watch."
                    "handle_fleet_deploy_watch_evaluate"
                )
            ),
            scopes=frozenset({"mcp:delegate"}),
            effect=Effect.READ,
        ),
    )


specs = operations

__all__ = [
    "FleetDeployWatchEvaluateParams",
    "FleetDeployWatchResult",
    "handle_fleet_deploy_watch_evaluate",
    "operations",
    "specs",
]
