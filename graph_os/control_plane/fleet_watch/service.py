"""Pure deploy-watch verdict decision (GRAPHOS-FLEET-R024.1).

GraphOS-owned parity port of the verdict rule previously computed inside
agent-utilities' ``agent_utilities.orchestration.deploy_watch.run_deploy_watch``,
which sat beside the agent runner in that package's ``orchestration/``
directory (GRAPHOS-FLEET-R024). That function's probe loop polls a fleet
observer on a timer and then reduces the probes it collected to one of three
verdicts; this module reproduces only that reduction, so it is testable
without a live observer, durable task queue or sleep loop:

* the first ``down`` probe ends the window as ``failed``, carrying that
  probe's own detail (a failure always wins, and later probes are never
  considered — the original loop ``break``\\ s on first ``down``);
* no ``down`` probe and at least one ``up`` probe ends it ``success``;
* no probe reached ``up`` or ``down`` at all ends it ``unobserved`` — the
  original module's documented refusal to roll back on zero evidence.

The probe-collection loop (observer polling, deadline/sleep bookkeeping) and
the failure-triggered rollback dispatch stay out of this slice; they are
GraphOS service-skeleton work tracked under GRAPHOS-FLEET-R024.2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProbeStatus = Literal["up", "down"]
Outcome = Literal["success", "failed", "unobserved"]

OUTCOME_SUCCESS: Outcome = "success"
OUTCOME_FAILED: Outcome = "failed"
OUTCOME_UNOBSERVED: Outcome = "unobserved"


class DeployProbe(BaseModel):
    """One fleet-observer sample taken during a deploy-watch window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ProbeStatus
    detail: str = Field(default="", max_length=500)


class DeployWatchVerdict(BaseModel):
    """The verdict a completed probe sequence reduces to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    detail: str
    healthy_probes: int = Field(ge=0)
    probes: int = Field(ge=0)


def evaluate_deploy_watch(
    service: str, probes: tuple[DeployProbe, ...]
) -> DeployWatchVerdict:
    """Reduce a deploy-watch probe sequence to its success/failed/unobserved verdict."""

    healthy = 0
    seen = 0
    for probe in probes:
        seen += 1
        if probe.status == "down":
            return DeployWatchVerdict(
                outcome=OUTCOME_FAILED,
                detail=probe.detail or "observed down",
                healthy_probes=healthy,
                probes=seen,
            )
        healthy += 1
    if healthy > 0:
        return DeployWatchVerdict(
            outcome=OUTCOME_SUCCESS,
            detail=f"sustained green ({healthy}/{seen} healthy probes)",
            healthy_probes=healthy,
            probes=seen,
        )
    return DeployWatchVerdict(
        outcome=OUTCOME_UNOBSERVED,
        detail=f"no observation for {service} during the window",
        healthy_probes=0,
        probes=seen,
    )
