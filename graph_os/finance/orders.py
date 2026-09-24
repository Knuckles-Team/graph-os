"""Live-order proposals and their human approval (EH-423).

A live order is never placed from GraphOS. GraphOS holds the two governance
steps in front of it:

* **Propose** (``graph_finance(action="propose_order")``, a verified caller
  holding ``finance:propose-order``, agents included): graph-os issues, on its
  service identity, a ``finance.order.approval`` ``ControlLease`` whose
  immutable grant is the order intent, its digest and the proposer's EG
  principal id. ``active`` means pending; only the proposer reads its status.
* **Approve / deny** (the operator console only -- plain gateway routes, never
  a tool): a signed-in person holding the exact ``finance:approve-live-order``
  scope, not the proposer, echoes the intent digest they were shown. Approval
  creates the D18 ``WriteBack`` change set ``finance-order:<approval id>``
  under the APPROVER's verified session -- EG refuses a change set whose
  ``actor`` is not the verified caller, so the record names who authorised the
  effect -- and then moves the lease to ``consumed``. Denial revokes it.

emerald-exchange places the order from that change set, once, after checking
it against the lease (``emerald_exchange.trading.live_orders``).
"""

from __future__ import annotations

import secrets
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.finance.models import OrderIntent, canonical_digest, principal_ref

__all__ = [
    "APPROVAL_KIND",
    "APPROVE_SCOPE",
    "OrderDecision",
    "OrderRefused",
    "approve_order",
    "deny_order",
    "order_change_set",
    "order_status",
    "propose_order",
]

APPROVAL_KIND = "finance.order.approval"
APPROVE_SCOPE = "finance:approve-live-order"
CONNECTOR_ID = "emerald-exchange"
#: A proposal must be approved, and the order executed, within this window.
DECISION_WINDOW_MS = 30 * 60 * 1000
_ORDER_FIELDS = ("symbol", "side", "qty", "order_type", "limit_price")


class OrderRefused(PermissionError):
    """The decision is not allowed; ``code`` is the stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OrderDecision(BaseModel):
    """What the console sends: WHICH proposal and the digest the person saw."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(pattern=r"^finance_order:[0-9a-f]{32}$")
    intent_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _grant(lease: dict[str, Any]) -> dict[str, Any]:
    grant = lease.get("grant")
    return grant if isinstance(grant, dict) else {}


async def propose_order(
    client: Any, claims: dict[str, Any], intent: OrderIntent, reason: str, now_ms: int
) -> dict[str, Any]:
    """Issue the pending approval for ``intent``; places nothing."""
    approval_id = f"finance_order:{secrets.token_hex(16)}"
    expires = now_ms + DECISION_WINDOW_MS
    answer = await client.control_leases.issue(
        tenant=str(claims["tenant"]),
        lease_id=approval_id,
        kind=APPROVAL_KIND,
        grant={
            "intent": intent.patch(),
            "intent_digest": intent.digest(),
            "proposer": principal_ref(str(claims["principal"])),
            "reason": reason,
        },
        issued_at_ms=now_ms,
        expires_at_ms=expires,
        hard_expires_at_ms=expires,
        idempotency_key=f"propose:{approval_id}",
    )
    if answer.get("outcome") != "issued":
        raise RuntimeError("the proposal could not be recorded")
    return {
        "approval_id": approval_id,
        "status": "pending_approval",
        "intent": intent.patch(),
        "intent_digest": intent.digest(),
        "expires_at_ms": expires,
        "next": "a person approves or denies it at the GraphOS operator console",
    }


async def order_status(
    client: Any, claims: dict[str, Any], approval_id: str
) -> dict[str, Any]:
    """The decision state of one of the caller's own proposals."""
    tenant = str(claims["tenant"])
    lease = await client.control_leases.get(tenant=tenant, lease_id=approval_id)
    proposer = principal_ref(str(claims["principal"]))
    if not lease or lease.get("kind") != APPROVAL_KIND:
        raise OrderRefused("ORDER_NOT_FOUND")
    if _grant(lease).get("proposer") != proposer:
        raise OrderRefused("ORDER_NOT_FOUND")
    states = {"active": "pending_approval", "consumed": "approved", "revoked": "denied"}
    return {
        "approval_id": approval_id,
        "status": states.get(str(lease.get("status")), "expired"),
        "intent": _grant(lease).get("intent"),
        "intent_digest": _grant(lease).get("intent_digest"),
        "expires_at_ms": lease.get("hard_expires_at_ms"),
    }


def _require_approver(claims: dict[str, Any], scopes: frozenset[str]) -> str:
    if APPROVE_SCOPE not in scopes:
        raise OrderRefused("ORDER_APPROVAL_SCOPE_REQUIRED")
    if claims.get("delegation"):
        raise OrderRefused("ORDER_APPROVAL_NOT_DELEGABLE")
    return principal_ref(str(claims["principal"]))


def _require_pending(
    lease: dict[str, Any] | None, decision: OrderDecision, now_ms: int
) -> dict[str, Any]:
    if not lease or lease.get("kind") != APPROVAL_KIND:
        raise OrderRefused("ORDER_NOT_FOUND")
    if lease.get("status") != "active" or now_ms >= int(lease["hard_expires_at_ms"]):
        raise OrderRefused("ORDER_NOT_PENDING")
    grant = _grant(lease)
    if grant.get("intent_digest") != decision.intent_digest:
        raise OrderRefused("ORDER_STALE_VIEW")
    return lease


def order_change_set(lease: dict[str, Any], tenant: str, approver: str) -> Any:
    """The D18 change set one approval authorises (digest filled in)."""
    from epistemic_graph.generated.write_back import (
        SourceChangeSet,
        WriteBackAuthorizationDecision,
        WriteBackAuthorizationMode,
    )

    approval_id = str(lease["lease_id"])
    grant = _grant(lease)
    patch = {name: grant["intent"].get(name) for name in _ORDER_FIELDS}
    decision = [approval_id, grant["intent_digest"], approver, lease["revision"]]
    change_set = SourceChangeSet(
        schema_version=1,
        change_set_id=f"finance-order:{approval_id}",
        change_set_digest="0" * 64,
        tenant_id=tenant,
        actor=approver,
        purpose="live order approved at the GraphOS operator console",
        connector_id=CONNECTOR_ID,
        source_instance_id=CONNECTOR_ID,
        entity_id=f"order:{approval_id}",
        base_source_version="absent",
        desired_patch=patch,
        field_scope=list(_ORDER_FIELDS),
        source_of_truth_rule="venue_accepts_order",
        field_provenance={name: f"approval:{approval_id}" for name in _ORDER_FIELDS},
        required_capability="finance:order-live",
        policy_digest=canonical_digest(
            "graphos/finance/live-order-policy/v1", [APPROVE_SCOPE, "two-person"]
        ),
        authorization=WriteBackAuthorizationDecision(
            mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
            authorization_ref=approval_id,
            decision_digest=canonical_digest(
                "graphos/finance/order-decision/v1", decision
            ),
            input_digest=grant["intent_digest"],
            output_digest=canonical_digest("graphos/finance/order-patch/v1", patch),
            authorized=True,
        ),
        idempotency_key=f"finance-order:{approval_id}",
        expires_at_ms=int(lease["hard_expires_at_ms"]),
        reconciliation_procedure="look the order up at the venue before any retry",
    )
    return change_set.model_copy(
        update={"change_set_digest": change_set.canonical_digest()}
    )


async def _transition(client: Any, tenant: str, lease: dict[str, Any], to: str) -> None:
    answer = await client.control_leases.transition(
        tenant=tenant,
        lease_id=str(lease["lease_id"]),
        expected_revision=int(lease["revision"]),
        to=to,
        idempotency_key=f"decide:{lease['lease_id']}:{to}",
    )
    if answer.get("outcome") != "applied":
        raise OrderRefused("ORDER_NOT_PENDING")


async def approve_order(
    client: Any,
    claims: dict[str, Any],
    scopes: frozenset[str],
    decision: OrderDecision,
    now_ms: int,
) -> dict[str, Any]:
    """Record the approver's change set, then mark the proposal approved."""
    from agent_connector_sdk.writeback.epistemic_graph import (
        EpistemicGraphWriteBackLedger,
    )

    approver = _require_approver(claims, scopes)
    tenant = str(claims["tenant"])
    lease = await client.control_leases.get(
        tenant=tenant, lease_id=decision.approval_id
    )
    lease = _require_pending(lease, decision, now_ms)
    if _grant(lease).get("proposer") == approver:
        raise OrderRefused("ORDER_OWN_PROPOSAL")
    change_set = order_change_set(lease, tenant, approver)
    await EpistemicGraphWriteBackLedger(client).create(change_set)
    await _transition(client, tenant, lease, "consumed")
    return {
        "approval_id": decision.approval_id,
        "status": "approved",
        "change_set_id": change_set.change_set_id,
        "next": (
            "emerald-exchange places it once: emerald_live_orders("
            f"action='execute_approved', approval_id='{decision.approval_id}')"
        ),
    }


async def deny_order(
    client: Any,
    claims: dict[str, Any],
    scopes: frozenset[str],
    decision: OrderDecision,
    now_ms: int,
) -> dict[str, Any]:
    """Revoke a pending proposal; nothing is recorded that could place it."""
    _require_approver(claims, scopes)
    tenant = str(claims["tenant"])
    lease = await client.control_leases.get(
        tenant=tenant, lease_id=decision.approval_id
    )
    lease = _require_pending(lease, decision, now_ms)
    await _transition(client, tenant, lease, "revoked")
    return {"approval_id": decision.approval_id, "status": "denied"}
