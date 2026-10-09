"""Parity tests for the ported deploy-watch verdict rule (GRAPHOS-FLEET-R024.1).

Each case reproduces one branch of agent-utilities'
``agent_utilities.orchestration.deploy_watch.run_deploy_watch`` post-probe
verdict block (read at
``agent_utilities/orchestration/deploy_watch.py``): a ``down`` probe fails
the window immediately with its own detail, a window with no ``down`` and at
least one ``up`` succeeds, and a window with no probes at all is
unobserved — never a rollback trigger on zero evidence.
"""

from __future__ import annotations

from graph_os.control_plane.fleet_watch.service import (
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    OUTCOME_UNOBSERVED,
    DeployProbe,
    evaluate_deploy_watch,
)


def test_a_down_probe_fails_immediately_with_its_own_detail() -> None:
    verdict = evaluate_deploy_watch(
        "checkout-api",
        (
            DeployProbe(status="up"),
            DeployProbe(status="down", detail="503 from /healthz"),
            # A probe after the down one must never be considered: the
            # original loop ``break``\ s on first ``down``.
            DeployProbe(status="up"),
        ),
    )
    assert verdict.outcome == OUTCOME_FAILED
    assert verdict.detail == "503 from /healthz"
    assert verdict.healthy_probes == 1
    assert verdict.probes == 2


def test_down_probe_with_no_detail_falls_back_to_observed_down() -> None:
    verdict = evaluate_deploy_watch("checkout-api", (DeployProbe(status="down"),))
    assert verdict.outcome == OUTCOME_FAILED
    assert verdict.detail == "observed down"


def test_sustained_green_window_succeeds() -> None:
    verdict = evaluate_deploy_watch(
        "checkout-api",
        (DeployProbe(status="up"), DeployProbe(status="up")),
    )
    assert verdict.outcome == OUTCOME_SUCCESS
    assert verdict.detail == "sustained green (2/2 healthy probes)"
    assert verdict.healthy_probes == 2
    assert verdict.probes == 2


def test_no_probes_at_all_is_unobserved_not_a_rollback_trigger() -> None:
    verdict = evaluate_deploy_watch("checkout-api", ())
    assert verdict.outcome == OUTCOME_UNOBSERVED
    assert verdict.detail == "no observation for checkout-api during the window"
    assert verdict.healthy_probes == 0
    assert verdict.probes == 0
