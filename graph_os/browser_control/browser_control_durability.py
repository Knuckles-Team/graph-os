"""Durable attended leases and deterministic browser call fences."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, cast

from agent_utilities.knowledge_graph.core.work_durability import (
    commit_result,
    get_work_item,
    submit_work_item_atomic,
)
from agent_utilities.security.persistence_privacy import persistence_reference

from graph_os.browser_control.browser_control_binding import (
    BINDING_PROPERTY_NAMES,
    BindingReferences,
    binding_properties,
)
from graph_os.browser_control.browser_control_common import canonical_internal_json


@dataclass(frozen=True, slots=True)
class CallIdentity:
    call_id: str
    item_id: str
    run_id: str
    request_digest: str


def new_lease_id() -> str:
    return f"browserlease_{secrets.token_hex(16)}"


def create_lease(
    authority: Any,
    *,
    lease_id: str,
    refs: BindingReferences,
    catalog_digest: str,
    tool_ids: tuple[str, ...],
    schema_digests: dict[str, str],
    policy_references: tuple[str, ...],
    issued_at: float,
    expires_at: float,
    hard_expires_at: float,
) -> None:
    properties = {
        "id": lease_id,
        "node_type": "BrowserControlLease",
        **binding_properties(refs),
        "catalog_digest": catalog_digest,
        "tool_ids": list(tool_ids),
        "schema_digests": schema_digests,
        "policy_references": list(policy_references),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "hard_expires_at": hard_expires_at,
        "status": "active",
    }
    if not authority.create_node_if_absent(lease_id, properties=properties):
        raise RuntimeError("lease identifier collision")


def read_lease(authority: Any, lease_id: str) -> dict[str, Any] | None:
    names = (
        *BINDING_PROPERTY_NAMES,
        "tool_ids",
        "schema_digests",
        "policy_references",
        "issued_at",
        "expires_at",
        "hard_expires_at",
        "status",
    )
    returns = ", ".join(f"l.{name} AS {name}" for name in names)
    rows = authority.query_cypher(
        f"MATCH (l:BrowserControlLease {{id: $id}}) RETURN {returns} LIMIT 2",
        {"id": lease_id},
    )
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    return rows[0]


def transition_lease(
    authority: Any,
    lease_id: str,
    *,
    current: dict[str, Any],
    updates: dict[str, Any],
) -> bool:
    """CAS one lease transition against its complete previously-read state."""

    return bool(authority.compare_and_set_node_fields(lease_id, current, updates))


def commit_call_outcome(
    engine: Any,
    *,
    item_id: str,
    claim: dict[str, Any],
    request_digest: str,
    status: str,
    effect: str,
    result_digest: str | None,
    error_code: str | None,
) -> str:
    """Commit one claimed browser call through the native WorkItem fence."""

    native_outcome = {
        "succeeded": "succeeded",
        "cancelled": "cancelled",
    }.get(status, "failed")
    result_ref = (
        persistence_reference("browser_result", result_digest, namespace=request_digest)
        if status == "succeeded" and result_digest
        else None
    )
    error_ref = (
        persistence_reference(
            "browser_error", error_code or effect, namespace=request_digest
        )
        if status != "succeeded"
        else None
    )
    return str(
        commit_result(
            engine,
            item_id,
            claim,
            outcome=native_outcome,
            result_ref=result_ref,
            error_ref=error_ref,
            retryable=False,
        )
    )


def call_identity(
    refs: BindingReferences,
    *,
    lease_id: str,
    request_id: str,
    tool_id: str,
    schema_digest: str,
    arguments: Any,
) -> CallIdentity:
    """Return call id, WorkItem id, run id, and exact request digest."""

    exact = canonical_internal_json(
        {
            "actor_reference": refs.actor_reference,
            "attended_arm_reference": refs.attended_arm_reference,
            "attended_arm_expires_at": refs.attended_arm_expires_at,
            "access_token_expires_at": refs.access_token_expires_at,
            "attended_arm_issued_at": refs.attended_arm_issued_at,
            "attended_auth_time": refs.attended_auth_time,
            "attended_acr": refs.attended_acr,
            "attended_issuer": refs.attended_issuer,
            "catalog_digest": refs.catalog_digest,
            "tool_scope_digest": refs.tool_scope_digest,
            "browser_session_reference": refs.browser_session_reference,
            "document_reference": refs.document_reference,
            "login_session_reference": refs.login_session_reference,
            "lease_id": lease_id,
            "origin_reference": refs.origin_reference,
            "policy_version": refs.policy_version,
            "principal_reference": refs.principal_reference,
            "registration_generation": refs.registration_generation,
            "request_id": request_id,
            "route_reference": refs.route_reference,
            "schema_digest": schema_digest,
            "tenant_reference": refs.tenant_reference,
            "tool_id": tool_id,
            "arguments": arguments,
        }
    )
    request_digest = hashlib.sha256(exact.encode()).hexdigest()
    call_id = f"browsercall_{request_digest[:32]}"
    return CallIdentity(
        call_id=call_id,
        item_id=f"workitem:browser_control:{request_digest}",
        run_id=f"browser-control:{request_digest}",
        request_digest=request_digest,
    )


def submit_call_fence(
    engine: Any,
    *,
    refs: BindingReferences,
    item_id: str,
    call_id: str,
    request_digest: str,
    lease_id: str,
    tool_id: str,
    schema_digest: str,
    policy_reference: str,
    confirmation_digest: str | None,
    admission_reference: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Atomically admit one deterministic browser-call WorkItem fence."""

    payload_reference, idempotency_key, metadata = _fence_inputs(
        refs=refs,
        call_id=call_id,
        request_digest=request_digest,
        lease_id=lease_id,
        tool_id=tool_id,
        schema_digest=schema_digest,
        policy_reference=policy_reference,
        confirmation_digest=confirmation_digest,
        admission_reference=admission_reference,
    )
    _, created = submit_work_item_atomic(
        engine,
        kind="browser.control.call",
        queue="browser_control",
        payload_ref=payload_reference,
        tenant=refs.tenant,
        resource_class="network_io",
        fairness_group=refs.actor_reference,
        max_attempts=1,
        idempotency_key=idempotency_key,
        description="Governed browser-local WebMCP call",
        created_by=refs.actor_reference,
        metadata=metadata,
        work_item_id=item_id,
    )
    row = get_work_item(engine, item_id)
    _validate_fence_row(
        row,
        refs=refs,
        item_id=item_id,
        payload_reference=payload_reference,
        idempotency_key=idempotency_key,
        metadata=metadata,
    )
    assert row is not None
    stored = cast(dict[str, Any], row["metadata"])
    owns_admission = stored.get("admission_reference") == admission_reference
    if created and not owns_admission:
        raise RuntimeError("created browser call fence changed admission owner")
    return created or owns_admission, row


def _fence_inputs(
    *,
    refs: BindingReferences,
    call_id: str,
    request_digest: str,
    lease_id: str,
    tool_id: str,
    schema_digest: str,
    policy_reference: str,
    confirmation_digest: str | None,
    admission_reference: str,
) -> tuple[str, str, dict[str, Any]]:
    payload_reference = persistence_reference(
        "browser_request", request_digest, namespace=refs.tenant_reference
    )
    idempotency_key = persistence_reference(
        "browser_call", request_digest, namespace=refs.actor_reference
    )
    metadata = {
        "call_id": call_id,
        "request_digest": request_digest,
        "lease_reference": persistence_reference(
            "browser_lease", lease_id, namespace=refs.tenant_reference
        ),
        "document_reference": refs.document_reference,
        "attended_arm_reference": refs.attended_arm_reference,
        "attended_arm_expires_at": refs.attended_arm_expires_at,
        "access_token_expires_at": refs.access_token_expires_at,
        "catalog_digest": refs.catalog_digest,
        "tool_scope_digest": refs.tool_scope_digest,
        "registration_generation": refs.registration_generation,
        "tool_id": tool_id,
        "schema_digest": schema_digest,
        "policy_reference": policy_reference,
        "confirmation_digest": confirmation_digest or "none",
        # A server-only nonce distinguishes an ambiguous create-then-read
        # failure from a concurrent replay of the same deterministic request.
        "admission_reference": admission_reference,
    }
    return payload_reference, idempotency_key, metadata


def _validate_fence_row(
    row: dict[str, Any] | None,
    *,
    refs: BindingReferences,
    item_id: str,
    payload_reference: str,
    idempotency_key: str,
    metadata: dict[str, Any],
) -> None:
    if row is None:
        raise RuntimeError("browser call fence was not durably readable")
    stored = row.get("metadata")
    envelope = {
        "id": item_id,
        "kind": "browser.control.call",
        "queue": "browser_control",
        "tenant": refs.tenant,
        "payload_ref": payload_reference,
        "idempotency_key": idempotency_key,
        "resource_class": "network_io",
        "fairness_group": refs.actor_reference,
        "max_attempts": 1,
        "created_by": refs.actor_reference,
    }
    if (
        not isinstance(stored, dict)
        or any(
            stored.get(key) != value
            for key, value in metadata.items()
            if key != "admission_reference"
        )
        or any(row.get(key) != value for key, value in envelope.items())
    ):
        raise PermissionError("browser call replay does not match its durable fence")


__all__ = [
    "CallIdentity",
    "call_identity",
    "commit_call_outcome",
    "create_lease",
    "new_lease_id",
    "read_lease",
    "submit_call_fence",
    "transition_lease",
]
