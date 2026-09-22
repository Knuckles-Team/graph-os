"""Atomic persistence for one RunTrace, ToolCall, and OutcomeEvaluation bundle.

The helper is intentionally small: callers supply one already-minted run id and
privacy-safe binding references, while the canonical trace ontology owns all
content digests and event sequencing.  A missing native typed-batch authority is
an error; safety-sensitive callers must never degrade to partial serial writes.

CONCEPT:AU-KG.audit.trace-id-assigned-at-emission
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_utilities.api.session import GraphSession
from agent_utilities.observability.trace_ontology import (
    OUTCOME_NODE_LABEL,
    TOOL_CALL_NODE_LABEL,
    TRACE_NODE_LABEL,
    TRACE_PRODUCED_OUTCOME_EDGE,
    TRACE_USED_TOOL_EDGE,
    outcome_id,
    outcome_properties,
    tool_call_properties,
    trace_id,
    trace_properties,
)

_ATTRIBUTE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_ATTRIBUTES = frozenset(
    {
        "actor_reference",
        "tenant_reference",
        "login_session_reference",
        "principal_reference",
        "browser_session_reference",
        "origin_reference",
        "document_reference",
        "route_reference",
        "registration_generation",
        "attended_arm_reference",
        "attended_arm_expires_at",
        "attended_arm_issued_at",
        "attended_auth_time",
        "attended_acr",
        "attended_issuer",
        "access_token_expires_at",
        "catalog_digest",
        "tool_scope_digest",
        "lease_reference",
        "fence_reference",
        "policy_reference",
        "policy_version",
        "confirmation_reference",
        "schema_digest",
        "cancellation_effect",
        "langfuse_status",
        "source_reference",
        "tool_id",
        "browser_result_digest",
        "browser_error_code",
    }
)


@dataclass(frozen=True, slots=True)
class TraceBundleReceipt:
    """Evidence that the native authority committed the complete bundle."""

    run_trace_id: str
    tool_call_id: str
    outcome_id: str


@dataclass(frozen=True, slots=True)
class _TraceBundle:
    run_trace_id: str
    tool_call_id: str
    outcome_id: str
    mutations: list[dict[str, Any]]


def _validated_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(attributes) - _ALLOWED_ATTRIBUTES
    if unknown:
        raise ValueError(f"unsupported trace bundle attributes: {sorted(unknown)!r}")
    validated: dict[str, Any] = {}
    for key, value in attributes.items():
        validated[key] = _validated_attribute(key, value)
    return validated


def _validated_attribute(key: str, value: Any) -> Any:
    if _ATTRIBUTE_NAME.fullmatch(key) is None:
        raise ValueError("trace bundle attribute name is invalid")
    if key == "registration_generation" and isinstance(value, int):
        return _validated_generation(value)
    if key in {
        "attended_arm_expires_at",
        "access_token_expires_at",
        "attended_arm_issued_at",
        "attended_auth_time",
    }:
        return _validated_timestamp(value)
    return _validated_reference(value)


def _validated_generation(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= 9_007_199_254_740_991:
        raise ValueError("registration generation is outside the JS-safe range")
    return value


def _validated_timestamp(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("trace bundle expiry must be a positive timestamp")
    return float(value)


def _validated_reference(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("trace bundle attributes must be bounded references")
    return value


def _build_bundle(
    *,
    run_id: str,
    agent_name: str,
    tool_name: str,
    arguments: Any,
    result: Any,
    error: Any,
    status: str,
    attributes: dict[str, Any],
    duration_ms: float | None,
    emitted_at: str,
) -> _TraceBundle:
    run_trace_id = trace_id(run_id)
    tool_call_id = f"toolcall:{run_trace_id.removeprefix('trace:')}:0"
    oid = outcome_id(run_id)
    trace_props = trace_properties(
        run_id=run_id,
        agent_name=agent_name,
        task=tool_name,
        status=status,
        timestamp=emitted_at,
        error=str(error or ""),
        duration_ms=duration_ms,
        result_preview=str(result or ""),
        execution_mode="browser_control",
    )
    event_sequence = int(trace_props["event_sequence"])
    tool_props = tool_call_properties(
        run_id=run_id,
        tool_name=tool_name,
        args=arguments,
        result=result,
        error=error,
        status=status,
        sequence=0,
        timestamp=emitted_at,
        event_sequence=event_sequence,
    )
    outcome_props = outcome_properties(
        run_id=run_id,
        status=status,
        timestamp=emitted_at,
        event_sequence=event_sequence,
        feedback=str(error or status),
    )
    for properties in (trace_props, tool_props, outcome_props):
        properties.update(attributes)
    mutations = [
        {
            "kind": "node",
            "id": run_trace_id,
            "node_type": TRACE_NODE_LABEL,
            "properties": trace_props,
        },
        {
            "kind": "node",
            "id": tool_call_id,
            "node_type": TOOL_CALL_NODE_LABEL,
            "properties": tool_props,
        },
        {
            "kind": "node",
            "id": oid,
            "node_type": OUTCOME_NODE_LABEL,
            "properties": outcome_props,
        },
        {
            "kind": "edge",
            "source": run_trace_id,
            "target": tool_call_id,
            "rel_type": TRACE_USED_TOOL_EDGE,
            "properties": {},
        },
        {
            "kind": "edge",
            "source": run_trace_id,
            "target": oid,
            "rel_type": TRACE_PRODUCED_OUTCOME_EDGE,
            "properties": {},
        },
    ]
    return _TraceBundle(run_trace_id, tool_call_id, oid, mutations)


def record_trace_bundle(
    engine: Any,
    *,
    session: GraphSession,
    run_id: str,
    agent_name: str,
    tool_name: str,
    arguments: Any,
    result: Any,
    error: Any,
    status: str,
    attributes: Mapping[str, Any],
    duration_ms: float | None = None,
    timestamp: str | None = None,
) -> TraceBundleReceipt:
    """Atomically upsert one canonical execution provenance bundle.

    Raw arguments, results, and errors are passed only to the canonical
    ontology builders, which persist their content digests and character counts
    while blanking content fields.  ``attributes`` is a closed set of already
    opaque references and bounded status values.
    """

    batch_write = getattr(engine, "batch_typed_mutations", None)
    if not callable(batch_write):
        raise RuntimeError("atomic trace bundle requires native typed-batch authority")
    safe_attributes = _validated_attributes(attributes)
    emitted_at = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bundle = _build_bundle(
        run_id=run_id,
        agent_name=agent_name,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        error=error,
        status=status,
        attributes=safe_attributes,
        duration_ms=duration_ms,
        emitted_at=emitted_at,
    )
    if not batch_write(bundle.mutations, session=session):
        raise RuntimeError("native typed-batch authority is unavailable")
    return TraceBundleReceipt(
        bundle.run_trace_id, bundle.tool_call_id, bundle.outcome_id
    )


__all__ = ["TraceBundleReceipt", "record_trace_bundle"]
