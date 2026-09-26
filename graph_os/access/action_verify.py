"""Caller-bound, advisory ActionPolicy preview for the served fleet API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_utilities.orchestration.action_policy import ActionPolicy, ActionRequest

from graph_os.api.invoke.pipeline import OperationRefused


@dataclass(frozen=True, slots=True)
class TenantActionPolicy:
    """A static policy selected for this exact caller client and tenant.

    The AU policy's engine-backed overrides are not tenant scoped. Only a
    policy with no engine can be previewed here; the host must select its
    policy file for the verified tenant through ``policy_for_caller``.
    """

    client: Any
    tenant: str
    policy: ActionPolicy


PolicyForCaller = Callable[[Any, str], TenantActionPolicy]


class ActionVerifier:
    """Preview an action with a host-supplied tenant policy, never authorize it."""

    def __init__(self, policy_for_caller: PolicyForCaller):
        if not callable(policy_for_caller):
            raise TypeError("a caller-bound policy factory is required")
        self._policy_for_caller = policy_for_caller

    async def verify(
        self,
        *,
        client: Any,
        tenant: str,
        actor_id: str,
        source: str,
        kind: str,
        target: str,
        params: dict[str, Any],
        reason: str,
    ) -> dict[str, str]:
        if (
            client is None
            or not isinstance(tenant, str)
            or not tenant
            or not isinstance(actor_id, str)
            or not actor_id
            or source != "manual"
        ):
            raise OperationRefused("UNAVAILABLE")
        bound = self._policy_for_caller(client, tenant)
        if (
            not isinstance(bound, TenantActionPolicy)
            or bound.client is not client
            or bound.tenant != tenant
            or not isinstance(bound.policy, ActionPolicy)
            or bound.policy.engine is not None
        ):
            raise OperationRefused("UNAVAILABLE")
        request = ActionRequest(
            kind=kind,
            target=target,
            params=params,
            source=source,
            reason=reason,
            actor_id=actor_id,
        )
        verdict = bound.policy.evaluate(request)
        return {
            "decision": verdict.decision,
            "tier": verdict.tier,
            "reason": verdict.reason,
            "invariant": verdict.invariant,
        }
