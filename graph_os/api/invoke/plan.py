"""Ten-minute, single-use confirmation leases held by epistemic-graph."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from graph_os.api.invoke.steps import OpError, VerifiedCaller

LEASE_KIND = "graphos.plan"
PLAN_TTL_MS = 600_000


def params_digest(params: Mapping[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanBinding:
    op_id: str
    params_digest: str
    principal: str
    tenant: str
    policy_revision: str
    registry_digest: str
    effect: str
    confirm: str

    def as_grant(self) -> dict[str, str]:
        return {
            "op_id": self.op_id,
            "params_digest": self.params_digest,
            "principal": self.principal,
            "tenant": self.tenant,
            "policy_revision": self.policy_revision,
            "registry_digest": self.registry_digest,
            "effect": self.effect,
            "confirm": self.confirm,
        }


def bind_plan(
    op: Any, params: Mapping[str, Any], caller: VerifiedCaller, registry_digest: str
) -> PlanBinding:
    return PlanBinding(
        op.id,
        params_digest(params),
        caller.principal,
        caller.tenant,
        caller.policy_revision,
        registry_digest,
        op.effect.value,
        op.confirm.value,
    )


class EgPlanStore:
    """Use EG's allowlisted control leases; caller supplies a service-authorized client."""

    def __init__(self, client: Any) -> None:
        self._leases = client.control_leases

    async def issue(self, binding: PlanBinding) -> str:
        plan_ref = f"graphos_plan:{secrets.token_hex(24)}"
        now_ms = int(time.time() * 1000)
        answer = await self._leases.issue(
            tenant=binding.tenant,
            lease_id=plan_ref,
            kind=LEASE_KIND,
            grant=binding.as_grant(),
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + PLAN_TTL_MS,
            hard_expires_at_ms=now_ms + PLAN_TTL_MS,
            idempotency_key=f"issue:{plan_ref}",
        )
        if answer.get("outcome") != "issued":
            raise RuntimeError("EG did not issue graphos.plan lease")
        return plan_ref

    async def consume(self, plan_ref: str, binding: PlanBinding) -> OpError | None:
        lease = await self._leases.get(tenant=binding.tenant, lease_id=plan_ref)
        if not lease or lease.get("kind") != LEASE_KIND:
            return OpError("PLAN_MISMATCH")
        if lease.get("status") != "active":
            return OpError("PLAN_EXPIRED")
        if int(lease.get("hard_expires_at_ms", 0)) <= int(time.time() * 1000):
            return OpError("PLAN_EXPIRED")
        grant = lease.get("grant")
        if not isinstance(grant, Mapping):
            return OpError("PLAN_MISMATCH")
        if (
            grant.get("registry_digest") != binding.registry_digest
            or grant.get("policy_revision") != binding.policy_revision
        ):
            return OpError("PLAN_STALE")
        if any(grant.get(key) != value for key, value in binding.as_grant().items()):
            return OpError("PLAN_MISMATCH")
        answer = await self._leases.transition(
            tenant=binding.tenant,
            lease_id=plan_ref,
            expected_revision=int(lease["revision"]),
            to="consumed",
            idempotency_key=f"consume:{plan_ref}",
        )
        return None if answer.get("outcome") == "applied" else OpError("PLAN_EXPIRED")
