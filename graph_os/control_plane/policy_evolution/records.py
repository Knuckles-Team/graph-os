"""Read-and-verify helpers over EG's generated ``PolicyEvolutionClient``.

graph-os never keeps a copy of a policy record. Every gate below reads the
record from EG (``PolicyRecordGet``, which re-derives the content-addressed id
and refuses a tampered body) and inspects the generated DTO it returns. The
checks are the graph-os half of the design: a control must be enabled AND its
named authorization scope must be one the verified caller holds; a promotion
needs an accepted, safety-passed, non-regressing held-out evaluation of
exactly the version being promoted, measured against the live version.

``PolicyRecordView.record`` is the generated ``kind``-tagged union whose
Python variants are ``PolicyEvolutionRecord<Kind>`` (renamed from
``PolicyRecord<Kind>`` to avoid a collision with change-envelope's
``PolicyRecord``). This module never names those classes: it reads only the
wire tag ``record.kind`` and the body ``record.record``, which the rename did
not change, so no generated-class name is part of graph-os's contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol

from .models import PolicyEvolutionControlError

__all__ = [
    "PolicyRecordReader",
    "require_accepted_evaluation",
    "require_control",
    "require_version",
]

Control = Literal["capture", "train", "promote"]


class PolicyRecordReader(Protocol):
    """The generated ``PolicyEvolutionClient.get`` graph-os consumes."""

    async def get(self, record_id: str) -> Any: ...


def _tag(value: object) -> str:
    return str(value.value) if isinstance(value, Enum) else str(value)


async def _record_body(reader: PolicyRecordReader, record_id: str, kind: str) -> Any:
    view = await reader.get(record_id)
    record = getattr(view, "record", None)
    if view is None or getattr(view, "record_id", None) != record_id:
        raise PolicyEvolutionControlError("POLICY_RECORD_MISSING", record_id)
    if record is None or _tag(getattr(record, "kind", "")) != kind:
        raise PolicyEvolutionControlError("POLICY_RECORD_MISSING", record_id)
    return record.record


async def require_control(
    reader: PolicyRecordReader,
    capability_id: str,
    control: Control,
    granted_scopes: frozenset[str],
) -> Any:
    """The attested capability, refusing unless ``control`` is usable here."""
    try:
        capability = await _record_body(reader, capability_id, "capability")
    except PolicyEvolutionControlError as missing:
        raise PolicyEvolutionControlError(
            "POLICY_CAPABILITY_MISSING", capability_id
        ) from missing
    setting = getattr(capability.controls, control)
    if not setting.enabled:
        raise PolicyEvolutionControlError(f"POLICY_{control.upper()}_DISABLED")
    if setting.scope is None or str(setting.scope) not in granted_scopes:
        raise PolicyEvolutionControlError(
            "POLICY_SCOPE_NOT_GRANTED", f"{control} needs its capability scope"
        )
    return capability


async def require_version(reader: PolicyRecordReader, version_id: str) -> Any:
    """The immutable ``ModelPolicyVersion`` a pointer may name."""
    return await _record_body(reader, version_id, "model_policy_version")


async def require_accepted_evaluation(
    reader: PolicyRecordReader,
    evaluation_id: str,
    version_id: str,
    live_version_id: str | None,
) -> Any:
    """A held-out evaluation that licenses promoting ``version_id``.

    It must evaluate exactly that version, be ``accepted`` with ``passed``
    safety and no task-success regression, and — when a version is already
    live — use the live version as its baseline, so the comparison is against
    what the promotion replaces.
    """
    evaluation = await _record_body(reader, evaluation_id, "policy_evaluation")
    if str(evaluation.version_id) != version_id:
        raise PolicyEvolutionControlError(
            "RELEASE_EVALUATION_MISMATCH", "evaluation names another version"
        )
    metrics = evaluation.metrics
    accepted = (
        _tag(evaluation.verdict) == "accepted"
        and _tag(evaluation.safety) == "passed"
        and metrics.task_success_ppm >= metrics.baseline_task_success_ppm
    )
    if not accepted:
        raise PolicyEvolutionControlError("RELEASE_EVALUATION_NOT_ACCEPTED")
    baseline = evaluation.baseline_version_id
    if live_version_id is not None and (
        baseline is None or str(baseline) != live_version_id
    ):
        raise PolicyEvolutionControlError(
            "RELEASE_EVALUATION_BASELINE_STALE",
            "the evaluation baseline is not the live version",
        )
    return evaluation
