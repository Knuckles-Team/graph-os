"""EG AgentAssemble request/result contract and fail-closed availability."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from epistemic_graph.generated.decision import (
    AbstainReasonUnresolvedCapabilityIri,
    AssemblyResult,
    CandidateSourceRecordAgentLibrary,
    DecisionOutcomeAbstained,
    DecisionQuestion,
    DecisionRecord,
    DerivationClass,
    EvidenceClass,
    ResolutionKind,
)

from graph_os import assembly
from graph_os.assembly import (
    AgentAssembly,
    AssemblyAbstained,
    AssemblyRequirements,
    AssemblyUnavailable,
)

_SESSION = SimpleNamespace(
    tenant="t1", engine_verified_context=lambda: {"tenant": "t1"}
)


def _requirements(**overrides: Any) -> AssemblyRequirements:
    fields: dict[str, Any] = {
        "text": "summarize the incident",
        "context_budget_tokens": 4096,
        "kinds": ("tool",),
    }
    fields.update(overrides)
    return AssemblyRequirements(**fields)


def test_unmapped_text_is_sent_only_as_a_digest() -> None:
    body = assembly.build_assembly_request("t1", _requirements())

    requirements = body["requirements"]
    assert requirements["tasks"] == []
    assert requirements["unmapped_task_digests"][0].startswith("sha256:")
    assert "summarize" not in str(body)
    assert requirements["constraints"]["context_budget_tokens"] == 4096
    assert body["policy"] == {"policy": "default"}


def test_declared_tasks_replace_the_text_digest() -> None:
    body = assembly.build_assembly_request(
        "t1", _requirements(task_iris=("eg:task/summarize",))
    )
    assert body["requirements"]["tasks"] == ["eg:task/summarize"]
    assert body["requirements"]["unmapped_task_digests"] == []


def test_budget_outside_the_contract_is_rejected() -> None:
    with pytest.raises(ValueError):
        _requirements(context_budget_tokens=10)


def test_solved_result_projects_agent_and_tool_slots() -> None:
    outcome = assembly.parse_assembly_result(
        {
            "record": {
                "record_id": "decision:1",
                "outcome": {
                    "outcome": "solved",
                    "slots": [
                        {
                            "slot": "a",
                            "component": {"component_id": "t1", "kind": "tool"},
                        },
                        {
                            "slot": "m",
                            "component": {
                                "component_id": "m1",
                                "kind": "model_profile",
                            },
                        },
                    ],
                },
            },
            "agent": {"agent_id": "expert"},
        }
    )
    assert outcome.agent_id == "expert"
    assert outcome.tool_ids == ("t1",)


def test_abstention_raises_with_its_reasons() -> None:
    with pytest.raises(AssemblyAbstained) as caught:
        assembly.parse_assembly_result(
            {
                "record": {
                    "record_id": "decision:2",
                    "outcome": {
                        "outcome": "abstained",
                        "reasons": [{"reason": "unmapped_task"}],
                    },
                }
            }
        )
    assert caught.value.reasons == ("unmapped_task",)


class _Client:
    def __init__(self, supported: bool) -> None:
        self._supported = supported

    async def supports(self, method: str) -> bool:
        return self._supported and method == "AgentAssemble"

    def use_verified_context(self, claims: Any) -> Any:
        return contextlib.nullcontext()


async def test_unserved_method_fails_closed(monkeypatch) -> None:
    async def sender(*args: Any) -> Any:
        raise AssertionError("must not send")

    monkeypatch.setattr(assembly, "_generated_sender", lambda: sender)
    with pytest.raises(AssemblyUnavailable):
        await AgentAssembly(lambda graph: _Client(False)).assemble(
            _SESSION, _requirements()
        )
    monkeypatch.setattr(assembly, "_generated_sender", lambda: None)
    with pytest.raises(AssemblyUnavailable):
        await AgentAssembly(lambda graph: _Client(True)).assemble(
            _SESSION, _requirements()
        )


async def test_served_method_sends_the_tenant_bound_request(monkeypatch) -> None:
    sent: list[Any] = []

    async def sender(client: Any, body: Any, graph: str) -> Any:
        sent.append((body, graph))
        return SimpleNamespace(
            payload={
                "record": {"record_id": "decision:3", "outcome": {"outcome": "solved"}},
                "agent": {"agent_id": "expert"},
            }
        )

    monkeypatch.setattr(assembly, "_generated_sender", lambda: sender)
    outcome = await AgentAssembly(lambda graph: _Client(True)).assemble(
        _SESSION, _requirements()
    )
    assert outcome.agent_id == "expert"
    assert sent[0][1] == "t1"
    assert sent[0][0]["request"]["tenant_id"] == "t1"


# EH-377(a) consumer audit: ``send_agent_assemble`` returns a typed
# ``AssemblyResult`` pydantic model, not a dict, but ``parse_assembly_result``
# checked ``isinstance(payload, dict)`` -- always False for a model -- so every
# call raised ``AssemblyUnavailable``. Fixed by decoding at the boundary
# (``_decoded_payload``). This drives a REAL generated ``AssemblyResult``
# instance (not the ``SimpleNamespace(payload={...})``/plain-dict fakes above,
# which is why the bug was not caught by them) through the real ``assemble()``
# call path.


def _real_abstained_assembly_result() -> AssemblyResult:
    record = DecisionRecord.model_construct(
        caller_principal="agent:t",
        candidate_source=CandidateSourceRecordAgentLibrary(
            source="agent_library", kinds=[]
        ),
        created_at_ms=0,
        derivation_class=DerivationClass.PROOF,
        derivations=[],
        eliminated=[],
        evidence_class=EvidenceClass.CLAIM,
        inputs_digest="sha256:" + "0" * 64,
        outcome=DecisionOutcomeAbstained(
            outcome="abstained",
            reasons=[
                AbstainReasonUnresolvedCapabilityIri(
                    reason="unresolved_capability_iri", iri="eg:cap/x"
                )
            ],
        ),
        premises=[],
        question=DecisionQuestion.ASSEMBLE,
        record_digest="sha256:" + "1" * 64,
        record_id="decision:real-1",
        resolution_kind=ResolutionKind.ABSTENTION,
        schema_version=1,
        tenant_id="t1",
        why_not=[],
    )
    return AssemblyResult.model_construct(record=record, schema_version=1)


async def test_a_real_typed_assembly_result_is_decoded_not_rejected(
    monkeypatch,
) -> None:
    async def sender(client: Any, body: Any, graph: str) -> Any:
        return _real_abstained_assembly_result()

    monkeypatch.setattr(assembly, "_generated_sender", lambda: sender)
    with pytest.raises(AssemblyAbstained) as caught:
        await AgentAssembly(lambda graph: _Client(True)).assemble(
            _SESSION, _requirements()
        )
    assert caught.value.record_id == "decision:real-1"
    assert caught.value.reasons == ("unresolved_capability_iri",)
