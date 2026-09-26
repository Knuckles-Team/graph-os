"""Approval queue reads and a fail-closed decision-authority seam.

The live queue is an ``action.approval`` ControlLease. EG's current
``TransitionControlLease`` has tenant/revision CAS and one-shot transitions,
but its request has neither an approver field nor an atomic hard-expiry fence.
The Train 5 EG candidate adds an atomic expiry fence and verified actor
evidence. Keep its adapter unbound until the native implementation, generated
contract, and caller-bound client are integrated and verified together. Reads
remain useful and decisions fail closed while those gates are pending.
The current safe bridge requires the same undelegated human to hold both the
GraphOS ``approvals:decide`` and EG ``lease:write`` grants; it never upgrades
claims or switches to a service actor. The dedicated Approver grant policy is
still an integration decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from graph_os.api.invoke.pipeline import OperationRefused
from graph_os.api.invoke.steps import VerifiedCaller

_KIND = "action.approval"


class ApprovalDecisionAuthority(Protocol):
    """Atomically decide a pending lease at its verified revision."""

    async def decide(
        self,
        *,
        client: Any,
        tenant: str,
        approval_id: str,
        expected_revision: int,
        decision: str,
        approver: str,
        idempotency_key: str,
        caller: VerifiedCaller,
    ) -> Mapping[str, Any]: ...


def _require_actor_preserving_lease_scope(
    caller: VerifiedCaller, *, tenant: str, approver: str
) -> None:
    """Use only a direct human who already holds both exact verified grants.

    GraphOS does not mint ``lease:write`` from ``approvals:decide``. EG must
    independently verify that lease grant on the same caller client. A service
    credential would record the service as transition actor, so it is refused.
    """
    if (
        not isinstance(caller, VerifiedCaller)
        or not caller.authenticated
        or caller.principal_kind != "human"
        or caller.delegated
        or caller.tenant != tenant
        or not caller.principal
        or not caller.policy_revision
    ):
        raise OperationRefused("PRINCIPAL_NOT_ALLOWED")
    claims = caller.engine_claims
    scopes = claims.get("scopes") if isinstance(claims, Mapping) else None
    if (
        not isinstance(scopes, (list, tuple, set, frozenset))
        or frozenset(scopes) != caller.effective_scopes
        or claims.get("principal") != caller.principal
        or claims.get("tenant") != tenant
        or claims.get("agent_id") != caller.principal
        or claims.get("policy_version") != caller.policy_revision
        or claims.get("delegation") not in ([], ())
    ):
        raise OperationRefused("UNAVAILABLE")
    if not {"approvals:decide", "lease:write"} <= caller.effective_scopes:
        raise OperationRefused("POLICY_DENIED")
    expected_actor = (
        "principal:sha256:" + hashlib.sha256(caller.principal.encode()).hexdigest()
    )
    if approver != expected_actor:
        raise OperationRefused("UNAVAILABLE")


class NativeLeaseDecisionAuthority:
    """Check EG's atomic transition receipt before reporting a decision."""

    async def decide(
        self,
        *,
        client: Any,
        tenant: str,
        approval_id: str,
        expected_revision: int,
        decision: str,
        approver: str,
        idempotency_key: str,
        caller: VerifiedCaller,
    ) -> Mapping[str, Any]:
        _require_actor_preserving_lease_scope(caller, tenant=tenant, approver=approver)
        if (
            decision not in {"approved", "denied"}
            or not isinstance(approver, str)
            or not approver.startswith("principal:sha256:")
            or not isinstance(idempotency_key, str)
            or not idempotency_key
        ):
            raise OperationRefused("UNAVAILABLE")
        target = "consumed" if decision == "approved" else "revoked"
        answer = await client.control_leases.transition(
            tenant=tenant,
            lease_id=approval_id,
            expected_revision=expected_revision,
            to=target,
            idempotency_key=idempotency_key,
        )
        if not isinstance(answer, Mapping):
            raise OperationRefused("UNAVAILABLE")
        outcome = answer.get("outcome")
        if outcome == "conflict":
            raise OperationRefused("PLAN_STALE")
        if outcome == "not_found":
            raise OperationRefused("POLICY_DENIED")
        if outcome != "applied":
            raise OperationRefused("UNAVAILABLE")
        lease = answer.get("lease")
        if not isinstance(lease, Mapping):
            raise OperationRefused("UNAVAILABLE")
        transition_ms = lease.get("transitioned_at_ms")
        hard_expiry_ms = lease.get("hard_expires_at_ms")
        expiry_ms = lease.get("expires_at_ms")
        if (
            lease.get("lease_id") != approval_id
            or lease.get("kind") != _KIND
            or lease.get("status") != target
            or lease.get("transition_actor") != approver
            or lease.get("revision") != expected_revision + 1
            or not isinstance(transition_ms, int)
            or isinstance(transition_ms, bool)
            or transition_ms <= 0
            or not isinstance(hard_expiry_ms, int)
            or not isinstance(expiry_ms, int)
            or (
                target == "consumed" and transition_ms >= min(expiry_ms, hard_expiry_ms)
            )
        ):
            raise OperationRefused("UNAVAILABLE")
        return {
            "approval_id": approval_id,
            "decision": decision,
            "revision": expected_revision + 1,
            "transitioned_at_ms": transition_ms,
        }


def _project(lease: Any, tenant: str) -> dict[str, Any]:
    if not isinstance(lease, Mapping):
        raise OperationRefused("UNAVAILABLE")
    if lease.get("kind") != _KIND or lease.get("tenant") not in (None, tenant):
        raise OperationRefused("POLICY_DENIED")
    if lease.get("tenant_ref") not in (None, tenant):
        raise OperationRefused("POLICY_DENIED")
    approval_id = lease.get("lease_id")
    if not isinstance(approval_id, str) or not approval_id.startswith(
        "action_approval:"
    ):
        raise OperationRefused("UNAVAILABLE")
    grant = lease.get("grant")
    if not isinstance(grant, Mapping):
        raise OperationRefused("UNAVAILABLE")
    status = lease.get("status")
    revision = lease.get("revision")
    kind = grant.get("kind")
    target = grant.get("target")
    if (
        not isinstance(status, str)
        or status not in {"active", "consumed", "revoked", "expired"}
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or not isinstance(kind, str)
        or not kind
        or len(kind) > 256
        or not isinstance(target, str)
        or not target
        or len(target) > 512
    ):
        raise OperationRefused("UNAVAILABLE")
    return {
        "approval_id": approval_id,
        "status": status,
        "revision": revision,
        "kind": kind,
        "target": target,
        "expires_at_ms": lease.get("hard_expires_at_ms"),
    }


class ApprovalService:
    """Use the caller's verified EG client and an optional decision authority."""

    def __init__(self, *, decision_authority: ApprovalDecisionAuthority | None = None):
        self._decision_authority = decision_authority

    @property
    def serving_safe(self) -> bool:
        """Only the EG atomic lease adapter may be bound in a serving process."""
        if self._decision_authority is None:
            return True
        if not isinstance(self._decision_authority, NativeLeaseDecisionAuthority):
            return False
        try:
            from epistemic_graph.generated.models import ControlLeaseView

            fields = ControlLeaseView.model_fields
        except (ImportError, AttributeError):
            return False
        return {"transition_actor", "transitioned_at_ms"} <= set(fields)

    async def call(
        self,
        operation: str,
        params: Mapping[str, Any],
        *,
        client: Any,
        tenant: str,
        approver: str,
        idempotency_key: str | None = None,
        caller: VerifiedCaller | None = None,
    ) -> dict[str, Any]:
        if operation == "approvals.list":
            page = await client.control_leases.list(
                tenant=tenant, kind=_KIND, status="active", limit=params["limit"]
            )
            if not isinstance(page, Mapping) or not isinstance(
                page.get("leases"), list
            ):
                raise OperationRefused("UNAVAILABLE")
            return {"pending": [_project(row, tenant) for row in page["leases"]]}
        if operation == "approvals.get":
            lease = await client.control_leases.get(
                tenant=tenant, lease_id=params["approval_id"]
            )
            if lease is None:
                raise OperationRefused("POLICY_DENIED")
            if not isinstance(lease, Mapping):
                raise OperationRefused("UNAVAILABLE")
            if lease.get("lease_id") != params["approval_id"]:
                raise OperationRefused("UNAVAILABLE")
            return _project(lease, tenant)
        if operation not in {"approvals.grant", "approvals.deny"}:
            raise OperationRefused("UNKNOWN_OP")
        if self._decision_authority is None:
            raise OperationRefused("UNAVAILABLE")
        if not idempotency_key:
            raise OperationRefused("UNAVAILABLE")
        if not isinstance(caller, VerifiedCaller):
            raise OperationRefused("UNAVAILABLE")
        lease = await client.control_leases.get(
            tenant=tenant, lease_id=params["approval_id"]
        )
        if lease is None:
            raise OperationRefused("POLICY_DENIED")
        current = _project(lease, tenant)
        if current["approval_id"] != params["approval_id"]:
            raise OperationRefused("UNAVAILABLE")
        if (
            current["status"] != "active"
            or current["revision"] != params["expected_revision"]
        ):
            raise OperationRefused("PLAN_STALE")
        result = await self._decision_authority.decide(
            client=client,
            tenant=tenant,
            approval_id=params["approval_id"],
            expected_revision=params["expected_revision"],
            decision="approved" if operation == "approvals.grant" else "denied",
            approver=approver,
            idempotency_key=idempotency_key,
            caller=caller,
        )
        if (
            not isinstance(result, Mapping)
            or result.get("approval_id") != params["approval_id"]
        ):
            raise OperationRefused("UNAVAILABLE")
        return dict(result)


async def execute(context: Any, params: Mapping[str, Any], op: Any) -> dict[str, Any]:
    """Composite binding behind the shared scope, principal, and MFA checks."""
    if op.id in {"approvals.grant", "approvals.deny"} and context.service_identity:
        raise OperationRefused("UNAVAILABLE")
    service = context.services.get("approvals")
    if not isinstance(service, ApprovalService):
        raise OperationRefused("UNAVAILABLE")
    value = await service.call(
        op.id,
        params,
        client=context.client,
        tenant=context.caller.tenant,
        approver=context.owner_ref,
        idempotency_key=context.idempotency_key,
        caller=context.caller,
    )
    return {"value": value}
