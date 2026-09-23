"""A2A-leased policy training: EG records + AU training path + graph-os lease (EH-347).

The records authority is the authenticated tenant EG client's generated
``policy_evolution`` surface; AU's ``PolicyTrainingPath`` runs the job
through graph-os's :class:`LeasedTrainerDispatcher`. Both fail closed with a
typed refusal until the connected EG and the installed AU publish them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from graph_os.control_plane.policy_evolution import (
    HostFacts,
    LeasedTrainerDispatcher,
    PolicyEvolutionControlError,
    PolicyTrainingAdmission,
    TrainerTransport,
    TrainingAdmissionRequest,
)

__all__ = [
    "compose_policy_training_path",
    "live_trainer_transport",
    "policy_records",
]


def policy_records(graph_client: Any) -> Any:
    """The tenant EG client's generated ``PolicyEvolutionClient``."""
    records = getattr(graph_client, "policy_evolution", None)
    if records is None:
        raise PolicyEvolutionControlError(
            "POLICY_EVOLUTION_UNAVAILABLE",
            "the connected epistemic-graph client has no policy_evolution surface",
        )
    return records


def compose_policy_training_path(
    graph_client: Any,
    admission: PolicyTrainingAdmission,
    request: TrainingAdmissionRequest,
    transport: TrainerTransport,
    hosts: HostFacts,
    clock_ms: Callable[[], int],
) -> Any:
    """AU's ``PolicyTrainingPath`` over EG records and the leased trainer."""
    try:
        from agent_utilities.harness.policy_evolution import PolicyTrainingPath
    except ImportError as exc:
        raise PolicyEvolutionControlError(
            "POLICY_TRAINING_PATH_UNAVAILABLE",
            "agent-utilities does not publish the policy training path",
        ) from exc
    trainer = LeasedTrainerDispatcher(admission, request, transport, hosts, clock_ms)
    return PolicyTrainingPath(policy_records(graph_client), trainer)


def live_trainer_transport(
    graph_client: Any, clock_ms: Callable[[], int]
) -> TrainerTransport:
    """The default A2A trainer transport over the tenant's EG server registry."""
    from .trainer import A2ATrainerTransport, EgTrainerRegistry

    return A2ATrainerTransport(EgTrainerRegistry(graph_client, clock_ms))
