"""Gap 5: the record gates against EG's real generated policy-evolution classes.

The views are the generated ``PolicyRecordView`` holding the renamed
``PolicyEvolutionRecord<Kind>`` variants, validated from wire-shaped JSON,
so the enums (``EvaluationVerdict``, ``SafetyOutcome``) and the omitted
defaulted fields (``controls``, ``enabled``) are exactly what EG sends.
"""

from __future__ import annotations

from typing import Any

import pytest
from epistemic_graph.generated.policy_evolution import (
    PolicyEvolutionRecordCapability,
    PolicyEvolutionRecordPolicyEvaluation,
    PolicyRecordView,
)

from graph_os.control_plane.policy_evolution import PolicyEvolutionControlError
from graph_os.control_plane.policy_evolution.records import (
    require_accepted_evaluation,
    require_control,
    require_version,
)

DIGEST = "a" * 64
CAP = "polcap:" + "1" * 64
V1 = "polver:" + "2" * 64
V2 = "polver:" + "3" * 64
EVAL = "poleval:" + "4" * 64
SCOPE = "policy:homelab"


def _capability(controls: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "vllm",
        "endpoint_ref": "endpoint:gb10",
        "base_checkpoint_digest": DIGEST,
        "tokenizer_digest": DIGEST,
        "decode_params_digest": DIGEST,
        "artifact_destination_ref": "artifact:lora",
        "logprobs": {"chosen_token": True},
        "probe_digest": DIGEST,
        "probed_at_ms": 1,
    }
    if controls is not None:
        body["controls"] = controls
    return {"kind": "capability", "record": body}


def _evaluation(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version_id": V2,
        "baseline_version_id": V1,
        "evaluation_set_digest": DIGEST,
        "evaluator_digest": DIGEST,
        "metrics": {
            "task_success_ppm": 900_000,
            "baseline_task_success_ppm": 800_000,
            "cost_micros": 1,
            "latency_p50_ms": 1,
            "latency_p95_ms": 2,
            "gpu_seconds": 1,
            "trace_completeness_ppm": 1_000_000,
            "unsupported_replay_mass_ppm": 0,
        },
        "safety": "passed",
        "verdict": "accepted",
    }
    body.update(overrides)
    return {"kind": "policy_evaluation", "record": body}


def _version() -> dict[str, Any]:
    return {
        "kind": "model_policy_version",
        "record": {
            "checkpoint_digest": DIGEST,
            "tokenizer_digest": DIGEST,
            "artifact_ref": "artifact:base",
            "origin": {"origin": "base"},
        },
    }


class _Reader:
    """``PolicyEvolutionClient.get`` answering real generated views."""

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._views = {
            record_id: PolicyRecordView.model_validate(
                {
                    "record_id": record_id,
                    "recorded_by": "principal:sha256:" + "5" * 64,
                    "recorded_at_ms": 1,
                    "record": record,
                }
            )
            for record_id, record in records.items()
        }

    async def get(self, record_id: str) -> PolicyRecordView | None:
        return self._views.get(record_id)


def test_views_carry_the_renamed_variant_classes() -> None:
    reader = _Reader({CAP: _capability(None), EVAL: _evaluation()})
    assert isinstance(reader._views[CAP].record, PolicyEvolutionRecordCapability)
    assert isinstance(reader._views[EVAL].record, PolicyEvolutionRecordPolicyEvaluation)


async def test_an_enabled_scoped_control_passes() -> None:
    reader = _Reader({CAP: _capability({"train": {"enabled": True, "scope": SCOPE}})})
    capability = await require_control(reader, CAP, "train", frozenset({SCOPE}))
    assert capability.provider == "vllm"


@pytest.mark.parametrize(
    "controls",
    [None, {}, {"train": {}}, {"train": {"enabled": False, "scope": SCOPE}}],
)
async def test_omitted_or_disabled_controls_are_off(
    controls: dict[str, Any] | None,
) -> None:
    reader = _Reader({CAP: _capability(controls)})
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await require_control(reader, CAP, "train", frozenset({SCOPE}))
    assert refused.value.code == "POLICY_TRAIN_DISABLED"


async def test_an_ungranted_scope_is_refused() -> None:
    reader = _Reader({CAP: _capability({"promote": {"enabled": True, "scope": SCOPE}})})
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await require_control(reader, CAP, "promote", frozenset({"kg:read"}))
    assert refused.value.code == "POLICY_SCOPE_NOT_GRANTED"


async def test_a_record_of_another_kind_is_missing() -> None:
    reader = _Reader({CAP: _evaluation()})
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await require_control(reader, CAP, "train", frozenset({SCOPE}))
    assert refused.value.code == "POLICY_CAPABILITY_MISSING"


async def test_the_real_enums_gate_the_evaluation() -> None:
    accepted = _Reader({EVAL: _evaluation()})
    evaluation = await require_accepted_evaluation(accepted, EVAL, V2, V1)
    assert evaluation.verdict.value == "accepted"
    for overrides in ({"verdict": "rejected"}, {"safety": "failed"}):
        reader = _Reader({EVAL: _evaluation(**overrides)})
        with pytest.raises(PolicyEvolutionControlError) as refused:
            await require_accepted_evaluation(reader, EVAL, V2, V1)
        assert refused.value.code == "RELEASE_EVALUATION_NOT_ACCEPTED"


async def test_a_stale_baseline_and_a_real_version_record() -> None:
    reader = _Reader({EVAL: _evaluation(baseline_version_id=None), V2: _version()})
    with pytest.raises(PolicyEvolutionControlError) as refused:
        await require_accepted_evaluation(reader, EVAL, V2, V1)
    assert refused.value.code == "RELEASE_EVALUATION_BASELINE_STALE"
    version = await require_version(reader, V2)
    assert version.artifact_ref == "artifact:base"
