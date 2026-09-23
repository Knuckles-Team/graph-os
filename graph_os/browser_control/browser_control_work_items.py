"""Browser-call WorkItem fences over the generated epistemic-graph client.

Every browser call is one durable EG WorkItem: admitted with
``SubmitWorkItem``, leased with ``ClaimWorkItem``, finished with
``CommitWorkItemResult`` or ``CancelWorkItem`` and read back with the typed
``GetWorkItem`` view. The engine owns tenant scoping, idempotency, lease
epochs and fencing; this module only shapes requests. It reaches the verbs
through the process engine's session-routed synchronous client and derives
the request context from the verified ambient session, so no caller-supplied
authority reaches EG. A missing verb fails closed.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

__all__ = [
    "TERMINAL_WORK_ITEM_STATUSES",
    "cancel_work_item",
    "claim_work_item",
    "commit_work_item",
    "get_work_item",
    "get_work_item_outcome",
    "submit_work_item",
]

TERMINAL_WORK_ITEM_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "dead_letter"}
)
_MAX_TENANT_IN_FLIGHT = 4096
_COMMIT_STATUSES = frozenset(
    {"committed", "retry_scheduled", "dead_letter", "noop", "fenced", "missing"}
)


class WorkItemAuthorityUnavailable(RuntimeError):
    """The connected engine does not serve a required WorkItem verb."""


def _verb(engine: Any, name: str) -> Any:
    compute = getattr(engine, "graph_compute", None)
    client = getattr(compute, "client", None)
    namespace = getattr(client, "work_items", None)
    verb = getattr(namespace, name, None)
    if not callable(verb):
        raise WorkItemAuthorityUnavailable(f"EG WorkItem verb {name!r} is unavailable")
    return verb


def _now_ms(now: float | None = None) -> int:
    return max(0, int((time.time() if now is None else now) * 1000))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def get_work_item(engine: Any, tenant: str, item_id: str) -> dict[str, Any] | None:
    """The tenant's typed view of one WorkItem, or ``None`` when not visible."""
    view = _verb(engine, "get")(tenant=tenant, work_item_id=item_id)
    return view if isinstance(view, dict) else None


def get_work_item_outcome(
    engine: Any, tenant: str, item_id: str
) -> dict[str, Any] | None:
    """The OutcomeEvaluation receipt the item's terminal commit bound, if any.

    EG resolves the receipt from the WorkItem row and refuses it unless its
    stored bytes still hash to the committed digest; ``None`` when the item
    is not visible or was committed without a provenance bundle.
    """
    value = _verb(engine, "get_outcome")(tenant=tenant, work_item_id=item_id)
    outcome = value.get("outcome") if isinstance(value, dict) else None
    return outcome if isinstance(outcome, dict) else None


def submit_work_item(
    engine: Any,
    *,
    work_item_id: str,
    idempotency_key: str,
    kind: str,
    input_ref: str,
    metadata: dict[str, Any],
    max_attempts: int = 1,
    attempt_fields: tuple[str, ...] = (),
) -> bool:
    """Admit one WorkItem; ``True`` when this call created it (not a replay).

    The command digest covers the logical command; ``attempt_fields`` names
    per-attempt metadata (such as an admission nonce) excluded from it, so a
    replay of the same call is recognized. A replay of the same key with a
    different command is refused by the engine; the error propagates.
    """
    from agent_utilities.api import resolve_session
    from agent_utilities.api.agent_control_adapters import request_context_for
    from agent_utilities.api.hosted_control_plane import (
        admission_digests,
        authentication_method_for,
    )

    session = resolve_session(required_scope="kg:write")
    policy_digest, catalog_digest, model_digest = admission_digests(session)
    command = {
        "work_item_id": work_item_id,
        "kind": kind,
        "input_ref": input_ref,
        "metadata": metadata,
        "max_attempts": max_attempts,
    }
    logical = {
        **command,
        "metadata": {k: v for k, v in metadata.items() if k not in attempt_fields},
    }
    result = _verb(engine, "submit")(
        {
            "schema_version": "1",
            "context": request_context_for(session, authentication_method_for(session)),
            "idempotency_key": idempotency_key,
            "command_digest": _digest(logical),
            "priority": 0,
            "depends_on": [],
            "policy_digest": policy_digest,
            "catalog_digest": catalog_digest,
            "model_digest": model_digest,
            "deadline_unix": None,
            "provenance_refs": [],
            "max_tenant_in_flight": 0,
            **command,
        }
    )
    if result.get("work_item_id") != work_item_id:
        raise RuntimeError("the WorkItem authority admitted an unrelated item")
    return bool(result.get("created"))


def claim_work_item(
    engine: Any,
    tenant: str,
    item_id: str,
    *,
    worker_ref: str,
    now: float,
    lease_ttl_s: float,
) -> dict[str, Any] | None:
    """Lease one exact ``ready`` WorkItem; ``None`` when the engine refuses."""
    result = _verb(engine, "claim")(
        {
            "schema_version": "1",
            "tenant_ref": tenant,
            "work_item_id": item_id,
            "queue_ref": None,
            "resource_class": None,
            "fairness_group": None,
            "worker_ref": worker_ref,
            "now_ms": _now_ms(now),
            "lease_ms": max(1, int(lease_ttl_s * 1000)),
            "max_tenant_in_flight": _MAX_TENANT_IN_FLIGHT,
        },
        idempotency_key=f"claim:{item_id}:{worker_ref}",
    )
    if not result.get("claimed"):
        return None
    return {**result, "tenant": tenant}


def commit_work_item(
    engine: Any,
    item_id: str,
    claim: dict[str, Any],
    *,
    outcome: str,
    result_ref: str | None = None,
    error_ref: str | None = None,
) -> str:
    """Commit a leased WorkItem's terminal outcome under its fence."""
    epoch = int(claim["lease_epoch"])
    result = _verb(engine, "commit_result")(
        tenant=str(claim["tenant"]),
        work_item_id=item_id,
        worker_id=str(claim["lease_holder_ref"]),
        lease_epoch=epoch,
        fencing_token=int(claim["fencing_token"]),
        idempotency_key=f"commit:{item_id}:{epoch}:{outcome}",
        outcome=outcome,
        now_ms=_now_ms(),
        result_ref=result_ref,
        error_ref=error_ref,
        retryable=False,
    )
    status = str((result or {}).get("status") or "").lower()
    if status not in _COMMIT_STATUSES:
        raise WorkItemAuthorityUnavailable(
            f"CommitWorkItemResult returned unknown status {status!r}"
        )
    return status


def cancel_work_item(
    engine: Any, tenant: str, item_id: str, *, reason_ref: str
) -> bool:
    """Cancel an unleased WorkItem; ``True`` when it is (now) cancelled."""
    result = _verb(engine, "cancel")(
        tenant=tenant,
        work_item_id=item_id,
        idempotency_key=f"cancel:{item_id}",
        now_ms=_now_ms(),
        reason_ref=reason_ref,
    )
    status = str((result or {}).get("status") or "").lower()
    if status == "noop":
        view = get_work_item(engine, tenant, item_id)
        return bool(view and view.get("status") == "cancelled")
    return status == "cancelled"
