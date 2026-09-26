"""MCPI-31 action preview never accepts caller-asserted actor or role."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_utilities.orchestration.action_policy import ActionPolicy
from pydantic import ValidationError

from graph_os.access.action_verify import ActionVerifier, TenantActionPolicy
from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.ops.action_verify import ActionVerifyParams, execute, specs
from graph_os.api.registry import PrincipalRule, Surface


def _context(
    authority: Any = None, *, principal_kind: str = "human", delegated: bool = False
):
    return SimpleNamespace(
        services={"action_verify": authority} if authority is not None else {},
        client=object(),
        caller=SimpleNamespace(
            principal="person:one",
            tenant="tenant-one",
            principal_kind=principal_kind,
            delegated=delegated,
        ),
    )


def test_contract_is_api_only_and_refuses_actor_or_source_input() -> None:
    op = specs()[0]
    assert op.id == "fleet.actions.verify"
    assert op.scopes == {"fleet:read"}
    assert op.principals is PrincipalRule.HUMAN_UNDELEGATED
    assert op.surfaces == {Surface.HTTP, Surface.CONSOLE}
    with pytest.raises(ValidationError):
        ActionVerifyParams(kind="restart_service", target="worker", source="reconciler")
    with pytest.raises(ValidationError):
        ActionVerifyParams(kind="restart_service", target="worker", actor_id="other")
    with pytest.raises(ValidationError):
        ActionVerifyParams(
            kind="restart_service", target="worker", params={"x": "a" * 17_000}
        )


@pytest.mark.asyncio
async def test_missing_authority_and_nonhuman_actor_fail_closed() -> None:
    op = specs()[0]
    params = ActionVerifyParams(kind="restart_service", target="worker").model_dump()
    with pytest.raises(OperationRefused) as refusal:
        await execute(_context(), params, op)
    assert refusal.value.code == "UNAVAILABLE"
    with pytest.raises(OperationRefused) as refusal:
        await execute(_context(object(), principal_kind="service"), params, op)
    assert refusal.value.code == "PRINCIPAL_NOT_ALLOWED"
    with pytest.raises(OperationRefused) as refusal:
        await execute(_context(object(), delegated=True), params, op)
    assert refusal.value.code == "PRINCIPAL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_bound_preview_receives_verified_identity_and_never_authorizes_effect() -> (
    None
):
    seen: list[dict[str, Any]] = []

    class Authority:
        async def verify(self, **kwargs: Any) -> dict[str, str]:
            seen.append(kwargs)
            return {
                "decision": "allow",
                "tier": "auto",
                "reason": "tier auto",
                "invariant": "",
            }

    context = _context(Authority())
    op = specs()[0]
    params = ActionVerifyParams(
        kind="restart_service", target="worker", params={"retry": 1}
    ).model_dump()
    result = await execute(context, params, op)
    assert result["value"]["decision"] == "allow"
    assert result["value"]["allowed"] is False
    assert seen == [
        {
            "client": context.client,
            "tenant": "tenant-one",
            "actor_id": "person:one",
            "source": "manual",
            "kind": "restart_service",
            "target": "worker",
            "params": {"retry": 1},
            "reason": "",
        }
    ]


@pytest.mark.asyncio
async def test_tenant_policy_preview_is_advisory_and_uses_verified_actor(
    tmp_path: Any,
) -> None:
    policy_path = tmp_path / "tenant-policy.yml"
    policy_path.write_text(
        "rules:\n  - kind: restart_service\n    target: worker\n    tier: auto\n",
        encoding="utf-8",
    )
    policy = ActionPolicy(engine=None, policy_path=policy_path)
    seen: list[tuple[Any, str]] = []

    def policy_for_caller(client: Any, tenant: str) -> TenantActionPolicy:
        seen.append((client, tenant))
        return TenantActionPolicy(client=client, tenant=tenant, policy=policy)

    context = _context(ActionVerifier(policy_for_caller))
    params = ActionVerifyParams(kind="restart_service", target="worker").model_dump()
    result = await execute(context, params, specs()[0])
    assert seen == [(context.client, "tenant-one")]
    assert result["value"]["decision"] == "allow"
    assert result["value"]["allowed"] is False


@pytest.mark.asyncio
async def test_tenant_policy_binding_rejects_foreign_client_tenant_and_engine() -> None:
    policy = ActionPolicy(engine=None)
    context = _context()
    params = ActionVerifyParams(kind="restart_service", target="worker").model_dump()
    for binding in (
        TenantActionPolicy(object(), "tenant-one", policy),
        TenantActionPolicy(context.client, "other-tenant", policy),
        TenantActionPolicy(context.client, "tenant-one", ActionPolicy(engine=object())),
    ):
        context.services["action_verify"] = ActionVerifier(
            lambda _client, _tenant, binding=binding: binding
        )
        with pytest.raises(OperationRefused) as refusal:
            await execute(context, params, specs()[0])
        assert refusal.value.code == "UNAVAILABLE"
