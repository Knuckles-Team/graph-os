"""Durable at-most-once admission for paper orders issued through GraphOS.

The EG lease is written before calling Emerald. A repeated key, including one
after a connector timeout or process restart, never dispatches again. The
request may therefore be indeterminate; an operator reconciles the paper
account before choosing a new key. The lease row remains as a collision fence
after its 24-hour expiry.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

from graph_os.finance.models import OrderIntent, principal_ref

PAPER_KIND = "finance.paper-order"
LEASE_WINDOW_MS = 24 * 60 * 60 * 1000


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _lease_id(tenant: str, owner: str, key: str) -> str:
    return "finance_paper:" + _digest([tenant, owner, key])[:32]


async def _reserve(context: Any, intent: OrderIntent) -> str:
    caller = context.caller
    key = context.idempotency_key
    if not isinstance(key, str) or not key:
        raise ValueError("paper order needs an idempotency key")
    tenant = caller.tenant
    owner = principal_ref(caller.principal)
    lease_id = _lease_id(tenant, owner, key)
    digest = intent.digest()
    now = time.time_ns() // 1_000_000
    result = await context.client.control_leases.issue(
        tenant=tenant,
        lease_id=lease_id,
        kind=PAPER_KIND,
        grant={"owner": owner, "intent_digest": digest},
        issued_at_ms=now,
        expires_at_ms=now + LEASE_WINDOW_MS,
        hard_expires_at_ms=now + LEASE_WINDOW_MS,
        idempotency_key=f"paper:{lease_id}",
    )
    if result.get("outcome") == "issued":
        return lease_id
    existing = await context.client.control_leases.get(tenant=tenant, lease_id=lease_id)
    grant = existing.get("grant") if isinstance(existing, dict) else None
    if (
        not existing
        or existing.get("kind") != PAPER_KIND
        or not isinstance(grant, dict)
    ):
        raise RuntimeError("paper request fence is unavailable")
    if grant.get("owner") != owner or grant.get("intent_digest") != digest:
        raise ValueError("paper idempotency key conflicts with a previous request")
    return ""


async def submit(context: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    """Reserve in EG, then invoke one paper-only admitted fleet call."""
    if (
        not context.service_identity
        or "finance:paper-trade" not in context.caller.effective_scopes
    ):
        raise PermissionError("paper trade authority is unavailable")
    from graph_os.fleet.shared_multiplexer import run_on_served_multiplexer

    intent = OrderIntent.model_validate(params.get("intent"))
    lease_id = await _reserve(context, intent)
    if not lease_id:
        return {
            "status": "indeterminate",
            "reason": "request already admitted; reconcile paper account",
        }

    async def dispatch(multiplexer: Any) -> Any:
        from graph_os.finance.sources import EMERALD_SERVER

        order = intent.patch()
        order["limit_price"] = order["limit_price"] or 0.0
        return await multiplexer.delegate_server_tool(
            server_name=EMERALD_SERVER,
            tool_name="emerald_paper_orders",
            arguments={"action": "submit", "request_id": lease_id, **order},
            timeout=30.0,
        )

    try:
        raw = await run_on_served_multiplexer(dispatch)
    except Exception:
        return {
            "status": "indeterminate",
            "reason": "connector outcome unknown; reconcile paper account",
        }
    try:
        result = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (ValueError, TypeError):
        return {
            "status": "indeterminate",
            "reason": "connector outcome unreadable; reconcile paper account",
        }
    if not isinstance(result, dict) or result.get("status") == "indeterminate":
        return {
            "status": "indeterminate",
            "reason": "connector outcome unknown; reconcile paper account",
        }
    return {"request_id": lease_id, **result}
