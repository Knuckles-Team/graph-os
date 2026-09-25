"""EH-609 registry contracts and release service authority."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph_os.api.ops import decide, policy, retrieval, swarm
from graph_os.api.registry import EgMethod, EgSchemaRef


def test_eg_bindings_match_contract_and_do_not_downgrade_effects() -> None:
    expected_scopes = {
        "Decide": "query:decide",
        "AgentAssemble": "agent:assemble-read",
        "DecisionCommit": "agent:decision-write",
        "DecisionFit": "admin:decision-fit",
        "DecisionEval": "admin:decision-eval",
        "MineRetrievalQuality": "mining:write",
        "GetContextView": "node:read",
    }
    for module in (decide, retrieval, swarm):
        for op in module.specs():
            assert isinstance(op.binding, EgMethod)
            assert isinstance(op.params, EgSchemaRef)
            assert isinstance(op.result, EgSchemaRef)
            assert op.params.path.endswith("/" + op.binding.service)
            assert op.result.path.endswith("/" + op.binding.service)
            assert expected_scopes[op.binding.service] in op.scopes
            if op.binding.service in {
                "DecisionCommit",
                "DecisionFit",
                "DecisionEval",
                "MineRetrievalQuality",
            }:
                assert op.effect.value != "read"
    commit = next(op for op in decide.specs() if op.id == "decide.commit")
    assert commit.principals.value == "service_only"
    for op in decide.specs():
        if op.effect.value == "admin":
            assert op.confirm.value == "console"
            assert op.principals.value == "human_undelegated"


def test_policy_release_models_reject_caller_authority_and_missing_cas() -> None:
    with pytest.raises(ValueError):
        policy.ReleaseStatusParams.model_validate(
            {"family": "x", "channel": "stable", "tenant_id": "other"}
        )
    with pytest.raises(ValueError):
        policy.ReleaseMoveParams.model_validate(
            {"family": "x", "channel": "stable", "next_version_id": "v2"}
        )
    assert policy.specs() == ()


@pytest.mark.asyncio
async def test_policy_release_refuses_unbound_engine() -> None:
    from graph_os.control_plane.policy_evolution import PolicyEvolutionControlError

    context = SimpleNamespace(client=object(), caller=SimpleNamespace(tenant="t"))
    with pytest.raises(
        PolicyEvolutionControlError, match="POLICY_EVOLUTION_UNAVAILABLE"
    ):
        await policy.handle_release(
            context,
            {"family": "f", "channel": "stable"},
            SimpleNamespace(id="policy.release.status"),
        )


@pytest.mark.asyncio
async def test_policy_release_uses_verified_tenant_scopes_and_existing_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Any] = []

    class FakeService:
        def __init__(self, records: Any, repository: Any) -> None:
            observed.append((records, repository))

        async def current(self, tenant: str, family: str, channel: str) -> None:
            observed.append((tenant, family, channel))
            return None

        async def apply(self, mutation: Any) -> Any:
            observed.append(mutation)
            return SimpleNamespace(model_dump=lambda **_: {"revision": 2})

    from graph_os.control_plane import policy_evolution

    monkeypatch.setattr(policy_evolution, "EgReleasePointerRepository", lambda c: c)
    monkeypatch.setattr(policy_evolution, "ModelPolicyReleaseService", FakeService)
    context = SimpleNamespace(
        client=SimpleNamespace(policy_evolution=object()),
        caller=SimpleNamespace(
            tenant="verified-tenant",
            effective_scopes=frozenset({"scope:verified"}),
        ),
    )
    result = await policy.handle_release(
        context,
        {
            "family": "f",
            "channel": "stable",
            "capability_id": "cap",
            "expected_revision": 1,
            "expected_version_id": "v1",
            "next_version_id": "v2",
            "evaluation_id": "evaluation",
            "change_ref": "change",
        },
        SimpleNamespace(id="policy.release.promote"),
    )
    assert result == {"pointer": {"revision": 2}}
    mutation = observed[-1]
    assert mutation.tenant_id == "verified-tenant"
    assert mutation.operation == "promote"
    assert mutation.granted_scopes == frozenset({"scope:verified"})
