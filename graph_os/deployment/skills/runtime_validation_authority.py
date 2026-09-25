"""GraphOS bundled-skill runtime authority logic."""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
from contextvars import copy_context
from typing import Any

from fastmcp.exceptions import ToolError

from .runtime_validation_core import (
    _AUTHORITY_EXPORT_FLUSH_CAP_SECONDS,
    _AUTHORITY_LEASE_SAFETY_SECONDS,
    _AUTHORITY_PARENT_INGESTION_CAP_SECONDS,
    _AUTHORITY_RENEWAL_TIMEOUT_SECONDS,
    _AUTHORITY_TRACE_PRECHECK_CAP_SECONDS,
    _MAX_TOOL_ITEMS,
    _PARENT_INGESTION_POLL_DELAY_SECONDS,
    _SAFE_ROUTE,
    _SYNC_CALL_ACTIVE,
    _SYNC_CALL_POISONED,
    _TRACE_MAX_PAGES,
    _TRACE_PAGE_LIMIT,
    _TRACE_TOOL_ERROR_RETRIES,
    _TRACE_TOOL_ERROR_RETRY_DELAY_SECONDS,
    TraceRecord,
    ValidationChildToolError,
)
from .runtime_validation_matrix import (
    _call_tool,
)


async def _verified_validation_session(
    headers: dict[str, str], *, minimum_ttl_seconds: int
) -> Any:
    """Validate one MCP bearer and mint sufficiently current graph authority."""

    if minimum_ttl_seconds < 0:
        raise ValueError("minimum_ttl_seconds_must_be_non_negative")

    authorization = str(
        headers.get("Authorization") or headers.get("authorization") or ""
    )
    scheme, separator, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not separator or not token.strip():
        raise RuntimeError("direct_identity_unavailable")
    from agent_utilities.security.request_identity import (
        actor_from_bearer_token,
        mint_graph_session,
    )

    actor = await actor_from_bearer_token(token.strip())
    session = mint_graph_session(actor)
    session.engine_verified_context()
    session.ensure_authority_current(minimum_ttl_seconds=minimum_ttl_seconds)
    return session


def minimum_campaign_authority_ttl_seconds(
    *,
    case_timeout: float,
    trace_timeout: float,
    shutdown_grace: float,
) -> int:
    """Return the lease needed for the campaign's longest bounded case.

    The lease spans the trace precheck, model call, exporter flush, exact-trace
    wait, parent-ingestion read-back, controlled shutdown, and a small expiry
    boundary margin. Keeping the calculation here makes deployment validation
    and runtime renewal share one definition instead of independent TTL floors.
    """

    windows = (case_timeout, trace_timeout, shutdown_grace)
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        for value in windows
    ):
        raise ValueError("campaign_authority_window_invalid")
    if case_timeout <= 0 or trace_timeout <= 0 or shutdown_grace < 0:
        raise ValueError("campaign_authority_window_invalid")
    bounded_seconds = (
        min(_AUTHORITY_TRACE_PRECHECK_CAP_SECONDS, trace_timeout)
        + case_timeout
        + min(_AUTHORITY_EXPORT_FLUSH_CAP_SECONDS, case_timeout)
        + trace_timeout
        + min(_AUTHORITY_PARENT_INGESTION_CAP_SECONDS, trace_timeout)
        + shutdown_grace
        + _AUTHORITY_LEASE_SAFETY_SECONDS
    )
    return math.ceil(bounded_seconds)


def _direct_case_minimum_authority_ttl(
    *, case_timeout: float, trace_timeout: float
) -> int:
    """Return the lease required to cover one bounded direct validation case."""

    return minimum_campaign_authority_ttl_seconds(
        case_timeout=case_timeout,
        trace_timeout=trace_timeout,
        shutdown_grace=0.0,
    )


async def _renew_direct_validation_session(
    *, expected_authority: dict[str, Any], minimum_ttl_seconds: int
) -> Any:
    """Mint current direct-case authority without changing its verified grant."""

    from agent_utilities.knowledge_graph.core.session import SessionExpiredError
    from agent_utilities.mcp.client_credentials import child_auth_header, get_provider

    async def mint_from_current_bearer() -> Any:
        headers = await _bounded_sync_call(
            lambda: child_auth_header({}),
            _AUTHORITY_RENEWAL_TIMEOUT_SECONDS,
        )
        return await _verified_validation_session(
            headers, minimum_ttl_seconds=minimum_ttl_seconds
        )

    try:
        session = await mint_from_current_bearer()
    except SessionExpiredError:
        # The provider normally refreshes within its expiry skew. A direct case
        # may require a longer lease than that skew, so proactively rotate the
        # bearer once and re-verify it instead of starting work that can expire.
        provider = get_provider()
        if provider is None:
            raise RuntimeError("direct_identity_renewal_unavailable") from None
        await _bounded_sync_call(
            lambda: provider.get_token(force=True),
            _AUTHORITY_RENEWAL_TIMEOUT_SECONDS,
        )
        session = await mint_from_current_bearer()

    if session.engine_verified_context() != expected_authority:
        raise RuntimeError("direct_identity_authority_changed")
    return session


async def _renew_delegated_validation_session(
    *, expected_authority: dict[str, Any], minimum_ttl_seconds: int
) -> Any:
    """Force a fresh bearer before one bounded delegated validation case."""

    from agent_utilities.mcp.client_credentials import child_auth_header, get_provider

    provider = get_provider()
    if provider is None:
        raise RuntimeError("delegated_identity_renewal_unavailable")
    await _bounded_sync_call(
        lambda: provider.get_token(force=True),
        _AUTHORITY_RENEWAL_TIMEOUT_SECONDS,
    )
    headers = await _bounded_sync_call(
        lambda: child_auth_header({}),
        _AUTHORITY_RENEWAL_TIMEOUT_SECONDS,
    )
    session = await _verified_validation_session(
        headers, minimum_ttl_seconds=minimum_ttl_seconds
    )
    if session.engine_verified_context() != expected_authority:
        raise RuntimeError("delegated_identity_authority_changed")
    return session


async def _ensure_tool(client: Any, tool: str, timeout: float) -> None:
    names = await _list_tool_names(client, timeout)
    if tool in names:
        return
    if "load_tools" not in names:
        raise RuntimeError("tool_loader_unavailable")
    await _call_tool(client, "load_tools", {"tools": [tool]}, timeout)
    names = await _list_tool_names(client, timeout)
    if tool not in names:
        raise RuntimeError("required_tool_unavailable")


async def _list_tool_names(client: Any, timeout: float) -> set[str]:
    """List a bounded MCP tool surface under the caller's wall-clock budget."""

    entries = await asyncio.wait_for(client.list_tools(), timeout=max(1.0, timeout))
    if not isinstance(entries, list) or len(entries) > _MAX_TOOL_ITEMS:
        raise RuntimeError("tool_catalog_invalid")
    names = {str(getattr(entry, "name", "") or "") for entry in entries}
    if "" in names or any(len(name) > 256 for name in names):
        raise RuntimeError("tool_catalog_invalid")
    return names


def _langfuse_tool_candidates(catalog: Any) -> list[str]:
    """Extract the safely named prefixed `langfuse_observability` entries."""

    candidates = []
    if isinstance(catalog, dict):
        for entry in catalog.get("tools") or []:
            if (
                isinstance(entry, dict)
                and entry.get("tool") == "langfuse_observability"
            ):
                candidates.append(str(entry.get("prefixed_name") or ""))
    return [name for name in candidates if _SAFE_ROUTE.fullmatch(name)]


async def _load_langfuse_tool(client: Any, timeout: float) -> str:
    """Discover and load Langfuse through Graph-OS, never a direct endpoint."""

    names = await _list_tool_names(client, timeout)
    if "list_catalog" not in names or "load_tools" not in names:
        raise RuntimeError("fleet_catalog_unavailable")
    catalog = await _call_tool(
        client,
        "list_catalog",
        {"server": "langfuse-mcp", "include_tools": True},
        timeout,
    )
    candidates = _langfuse_tool_candidates(catalog)
    if len(candidates) != 1:
        raise RuntimeError("langfuse_tool_discovery_failed")
    if candidates[0] not in names:
        await _call_tool(client, "load_tools", {"tools": candidates}, timeout)
        names = await _list_tool_names(client, timeout)
        if candidates[0] not in names:
            raise RuntimeError("langfuse_tool_load_failed")
    return candidates[0]


async def _await_sync_worker(
    completed: threading.Event, poisoned: threading.Event, timeout: float
) -> None:
    """Await the single SDK worker, poisoning the slot if it is abandoned."""

    try:
        deadline = time.monotonic() + max(1.0, timeout)
        while not completed.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if completed.is_set():
                    break
                poisoned.set()
                raise TimeoutError("blocking_sdk_timeout")
            await asyncio.sleep(min(0.05, remaining))
    except BaseException:
        if not completed.is_set():
            poisoned.set()
        raise


async def _bounded_sync_call(function: Any, timeout: float) -> Any:
    """Run at most one blocking SDK call and fail closed after abandonment.

    CPython cannot safely terminate a thread blocked inside a third-party SDK.
    A timeout or caller cancellation therefore poisons this certification
    process: the daemon may finish during shutdown, but no second SDK worker is
    admitted and the validation run aborts instead of accumulating work beyond
    its budget.
    """

    active_guard = _SYNC_CALL_ACTIVE
    poisoned = _SYNC_CALL_POISONED
    if poisoned.is_set():
        raise RuntimeError("blocking_sdk_worker_abandoned")
    if not active_guard.acquire(blocking=False):
        raise RuntimeError("blocking_sdk_worker_active")
    if poisoned.is_set():
        active_guard.release()
        raise RuntimeError("blocking_sdk_worker_abandoned")

    outcome: list[tuple[bool, Any]] = []
    completed = threading.Event()
    caller_context = copy_context()

    def invoke() -> None:
        try:
            outcome.append((True, caller_context.run(function)))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller task
            outcome.append((False, exc))
        finally:
            active_guard.release()
            completed.set()

    try:
        threading.Thread(
            target=invoke,
            daemon=True,
            name="skill-validation-sdk",
        ).start()
    except BaseException:
        active_guard.release()
        raise
    await _await_sync_worker(completed, poisoned, timeout)
    succeeded, value = outcome[0]
    if succeeded:
        return value
    raise value


async def _verify_langfuse_posture(
    client: Any, langfuse_tool: str, timeout: float
) -> None:
    """Prove the mounted child is enforcing metadata-only retention."""

    posture = await _call_tool(
        client,
        langfuse_tool,
        {"action": "runtime_posture"},
        timeout,
    )
    if posture != {
        "content_capture_enabled": False,
        "metadata_only": True,
    }:
        raise RuntimeError("langfuse_content_posture_invalid")


def _expected_trace_name(run_id: str, tenant_id: str) -> str:
    """Derive the exact opaque trace name emitted for a runtime run."""

    from agent_utilities.usage.privacy import normalize_run_id

    name = f"graph_run:{normalize_run_id(run_id, tenant_id=tenant_id)}"
    if not re.fullmatch(r"graph_run:pref_run_[a-f0-9]{64}", name):
        raise RuntimeError("trace_expected_name_invalid")
    return name


def _trace_row_evidence(row: dict[str, Any]) -> dict[str, str]:
    """Extract only the closed opaque evidence contract from trace metadata."""

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    patterns = {
        "run_ref": re.compile(r"pref_run_[a-f0-9]{64}"),
        "model_ref": re.compile(r"pref_model_[a-f0-9]{64}"),
        "skill_ref": re.compile(r"pref_skill_[a-f0-9]{64}"),
        "skill_body_ref": re.compile(r"pref_skill_body_[a-f0-9]{64}"),
        "model_class": re.compile(r"(?:economy|standard)"),
    }
    evidence: dict[str, str] = {}
    for key, pattern in patterns.items():
        if key not in metadata:
            continue
        value = metadata[key]
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise RuntimeError("trace_evidence_invalid")
        evidence[key] = value
    return evidence


def _trace_list_arguments(
    page: int, from_timestamp: str | None, expected_name: str
) -> dict[str, Any]:
    """Build one bounded, exact-name `trace_list` request for a single page."""

    args: dict[str, Any] = {
        "action": "trace_list",
        "page": page,
        # Cases run sequentially and each window expects one run-linked trace.
        # Small pages stay below GraphOS's delegated-value boundary even when
        # the shared project contains content-heavy automatic telemetry.
        "limit": _TRACE_PAGE_LIMIT,
        "order_by": "timestamp.desc",
        "fields": "core,basic,metadata",
    }
    if from_timestamp:
        args["from_timestamp"] = from_timestamp
    # Filter at the provider boundary so unrelated shared-project traffic
    # cannot consume the bounded page window or expand metadata exposure.
    args["name"] = expected_name
    return args


def _collect_trace_rows(
    snapshot: dict[str, TraceRecord], rows: list[Any], expected_name: str
) -> None:
    """Retain only exact-name rows and their closed evidence metadata."""

    for row in rows:
        if not isinstance(row, dict):
            continue
        trace_id = str(row.get("id") or "")
        name = str(row.get("name") or "")
        if trace_id and len(trace_id) <= 256 and name == expected_name:
            snapshot[trace_id] = TraceRecord(
                name=name,
                evidence=_trace_row_evidence(row),
            )


async def _trace_snapshot(
    client: Any,
    langfuse_tool: str,
    timeout: float,
    *,
    from_timestamp: str | None = None,
    expected_name: str,
) -> dict[str, TraceRecord]:
    if not re.fullmatch(r"graph_run:pref_run_[a-f0-9]{64}", expected_name):
        raise RuntimeError("trace_expected_name_invalid")
    snapshot: dict[str, TraceRecord] = {}
    deadline = time.monotonic() + max(1.0, timeout)
    max_pages = _TRACE_MAX_PAGES if from_timestamp else 1
    for page in range(1, max_pages + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("trace_snapshot_timeout")
        args = _trace_list_arguments(page, from_timestamp, expected_name)
        payload = await _call_tool(client, langfuse_tool, args, min(remaining, timeout))
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("trace_snapshot_invalid")
        _collect_trace_rows(snapshot, rows, expected_name)
        if len(rows) < _TRACE_PAGE_LIMIT:
            return snapshot
    if from_timestamp:
        raise RuntimeError("trace_snapshot_boundary_exceeded")
    return snapshot


async def _next_transient_retry(attempts: int, deadline: float) -> int | None:
    """Charge one bounded retry for a typed child-tool failure.

    Returns the new attempt count after sleeping the backoff, or ``None`` when
    the retry budget or the caller's deadline is exhausted and the typed
    failure must propagate as a certification gate.
    """

    if attempts >= _TRACE_TOOL_ERROR_RETRIES:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    await asyncio.sleep(min(_TRACE_TOOL_ERROR_RETRY_DELAY_SECONDS, remaining))
    return attempts + 1


def _matched_expected_trace(
    current: dict[str, TraceRecord], expected_evidence: dict[str, str]
) -> str | None:
    """Return the single exact-evidence trace id, or None while none exists."""

    matching = sorted(current)
    if len(matching) == 1:
        record = current[matching[0]]
        if any(
            record.evidence.get(key) != value
            for key, value in expected_evidence.items()
        ):
            raise RuntimeError("trace_evidence_mismatch")
        return matching[0]
    if len(matching) > 1:
        raise RuntimeError("trace_run_identifier_ambiguous")
    return None


async def _wait_for_expected_trace(
    client: Any,
    langfuse_tool: str,
    started_at: str,
    expected_name: str,
    expected_evidence: dict[str, str],
    timeout: float,
) -> tuple[str, str]:
    """Require one exact run trace with the case's controlled evidence metadata."""

    if not expected_evidence or expected_evidence.get("run_ref") != (
        expected_name.removeprefix("graph_run:")
    ):
        raise RuntimeError("trace_expected_evidence_invalid")

    deadline = time.monotonic() + timeout
    transient_tool_errors = 0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            current = await _trace_snapshot(
                client,
                langfuse_tool,
                min(15.0, remaining),
                from_timestamp=started_at,
                expected_name=expected_name,
            )
        except (ToolError, ValidationChildToolError):
            # An exact-name trace read is idempotent, and GraphOS may fail the
            # outer call after a successful provider read when its mandatory
            # parent ChangeEnvelope races another graph writer. Retry only the
            # typed child-tool failure, keep the attempt count bounded, and let
            # persistent provider/ingestion failures remain certification gates.
            attempts = await _next_transient_retry(transient_tool_errors, deadline)
            if attempts is None:
                raise
            transient_tool_errors = attempts
            continue
        matched = _matched_expected_trace(current, expected_evidence)
        if matched is not None:
            return matched, "run-evidence"
        await asyncio.sleep(1.0)
    raise TimeoutError("trace_not_observed")


def _require_parent_ingestion_inputs(expected_name: str, timeout: float) -> None:
    """Require an exact opaque trace name and a finite, positive time budget."""

    if not re.fullmatch(r"graph_run:pref_run_[a-f0-9]{64}", expected_name):
        raise RuntimeError("trace_expected_name_invalid")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("trace_parent_ingestion_timeout_invalid")


def _parent_ingestion_query(expected_name: str) -> dict[str, Any]:
    """Build the bounded, identity-retaining parent-ingestion readback query."""

    return {
        "query": (
            "MATCH (n:Trace) WHERE n.name = $name "
            "RETURN n.id AS id, n.name AS name LIMIT 2"
        ),
        "params": json.dumps({"name": expected_name}, separators=(",", ":")),
        "scope": "local",
    }


async def _verify_parent_ingested_trace(
    client: Any,
    expected_name: str,
    timeout: float,
) -> int:
    """Require exactly one parent-mediated KG node for an exact opaque trace."""

    _require_parent_ingestion_inputs(expected_name, timeout)
    arguments = _parent_ingestion_query(expected_name)
    deadline = time.monotonic() + timeout
    transient_tool_errors = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("trace_parent_ingestion_not_observed")
        try:
            payload = await _call_tool(
                client,
                "graph_query",
                arguments,
                min(15.0, remaining),
            )
        except (ToolError, ValidationChildToolError):
            attempts = await _next_transient_retry(transient_tool_errors, deadline)
            if attempts is None:
                raise
            transient_tool_errors = attempts
            continue
        count = _parent_ingested_trace_count(payload, expected_name=expected_name)
        if count == 1:
            return count
        if count != 0:
            raise RuntimeError("trace_parent_ingestion_mismatch")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("trace_parent_ingestion_not_observed")
        await asyncio.sleep(min(_PARENT_INGESTION_POLL_DELAY_SECONDS, remaining))


def _graph_query_trace(payload: Any) -> dict[str, Any] | None:
    """Return the single closed `graph_query` step of an EvidenceBundle trace."""

    if not isinstance(payload, dict):
        return None
    reasoning_trace = payload.get("reasoning_trace")
    if not isinstance(reasoning_trace, list) or any(
        not isinstance(item, dict) for item in reasoning_trace
    ):
        return None
    query_traces = [
        item for item in reasoning_trace if item.get("step") == "graph_query"
    ]
    if len(query_traces) != 1:
        return None
    trace = query_traces[0]
    return trace if set(trace) == {"step", "payload"} else None


def _graph_query_rows(payload: Any) -> list[Any] | None:
    """Return the bounded row projection of GraphQuery's EvidenceBundle trace."""

    trace = _graph_query_trace(payload)
    if trace is None:
        return None
    aggregate = trace.get("payload")
    if not isinstance(aggregate, dict) or set(aggregate) != {"rows"}:
        return None
    rows = aggregate.get("rows")
    if not isinstance(rows, list) or len(rows) > 2:
        return None
    return rows


def _governed_trace_row(row: Any, expected_name: str) -> bool:
    """Accept only a two-field row whose node identity and name are governed."""

    if not isinstance(row, dict) or set(row) != {"id", "name"}:
        return False
    node_id = row.get("id")
    if (
        not isinstance(node_id, str)
        or re.fullmatch(r"langfuse:trace:[a-f0-9]{32}", node_id) is None
    ):
        return False
    return row.get("name") == expected_name


def _parent_ingested_trace_count(payload: Any, *, expected_name: str) -> int | None:
    """Count only governed trace-id rows in GraphQuery's EvidenceBundle trace.

    Public graph reads must retain node identity so tenant, ACL, visibility, and
    audit enforcement can govern every returned row.  The query is bounded at
    two rows: one is the required materialization, zero is missing, and two
    proves an ambiguous duplicate.  Accept only that closed projection; an
    aggregate without node identity, a similarly named claim, or a widened row
    is not proof of parent-mediated ingestion.
    """

    rows = _graph_query_rows(payload)
    if rows is None:
        return None
    if not all(_governed_trace_row(row, expected_name) for row in rows):
        return None
    return len(rows)
