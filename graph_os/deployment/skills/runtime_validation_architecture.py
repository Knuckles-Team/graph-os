"""GraphOS bundled-skill runtime architecture logic."""

from __future__ import annotations

import json
import math
import time
from typing import Any

from graph_os.deployment.skills.validation import (
    _ARCHITECTURE_ACTIVE_STATUS,
    _ARCHITECTURE_AUTHORITY_STATE,
    _ARCHITECTURE_MANIFEST_PATH,
    _ARCHITECTURE_SOURCE_AUTHORITY,
)

from .runtime_validation_core import (
    _ARCHITECTURE_DISCOVERY_FIELDS,
    _ARCHITECTURE_DISCOVERY_HEADER,
    _ARCHITECTURE_DISCOVERY_TRAILER,
    _ARCHITECTURE_LAYOUT_REQUIREMENTS,
    _ARCHITECTURE_OPERATION_TIMEOUT_SECONDS,
    _ARCHITECTURE_PHASE_OPERATIONS,
    _ARCHITECTURE_SKILL,
    _ARCHITECTURE_WORKFLOW_SCENARIOS,
    _DIGEST,
    _SAFE_ERROR,
    ArchitectureCandidateIdentity,
    ArchitectureDiscoveryRow,
    ArchitectureRegistryRow,
    ArchitectureScenarioObservation,
    CaseResult,
    GraphOperationObservation,
    ValidationCase,
    _canonical_bytes,
    _canonical_owner_path,
    _digest_bytes,
)
from .runtime_validation_matrix import (
    _call_tool,
)


def architecture_workflow_scenarios() -> tuple[str, ...]:
    """Return the deterministic RF-021 scenario labels in matrix order."""

    return _ARCHITECTURE_WORKFLOW_SCENARIOS + _ARCHITECTURE_LAYOUT_REQUIREMENTS


def _architecture_operation_specs(
    candidate: ArchitectureCandidateIdentity,
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    """Build candidate-bound requests for the three real Graph-OS operations."""

    query = (
        "MATCH (component:ArchitectureComponent {component_id: $component_id})"
        "-[:IMPLEMENTS]->"
        "(capability:ArchitectureCapability {capability_id: $capability_id}) "
        "WHERE component.source_repository_id = $source_repository_id "
        "RETURN component.component_id AS component_id, "
        "component.component_kind AS component_kind, "
        "capability.capability_id AS capability_id, "
        "capability.implementation_authority_component_id "
        "AS implementation_authority_component_id, "
        "component.parent_component_id AS parent_component_id, "
        "component.parent_layer AS parent_layer, "
        "component.source_authority AS source_authority, "
        "component.authority_state AS authority_state, "
        "component.status AS status, "
        "component.source_workspace_manifest AS source_workspace_manifest, "
        "component.source_repository_id AS source_repository_id, "
        "component.source_repository_path AS source_repository_path, "
        "component.source_manifest_path AS source_manifest_path, "
        "component.source_revision AS source_revision, "
        "component.source_digest AS source_digest, "
        "component.authority_signature AS authority_signature, "
        "component.behavioral_signature AS behavioral_signature, "
        "component.dependency_signature AS dependency_signature, "
        "component.identity_policy_digest AS identity_policy_digest, "
        "component.target_item_refs AS target_item_refs, "
        "component.owned_source_roots AS owned_source_roots, "
        "component.public_contract_roots AS public_contract_roots, "
        "component.test_roots AS test_roots, "
        "component.generated_roots AS generated_roots, "
        "component.shared_paths AS shared_paths, "
        "component.replaces AS replaces, "
        "component.deletion_proof AS deletion_proof LIMIT 2"
    )
    params = json.dumps(
        {
            "capability_id": candidate.capability_id,
            "component_id": candidate.component_id,
            "source_repository_id": candidate.source_repository_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    exact_identity = f"{candidate.component_id} {candidate.capability_id}"
    caller_query = (
        f"live callers of {candidate.capability_id} implemented by "
        f"{candidate.component_id}"
    )
    return (
        (
            "registry_lookup",
            "graph_query",
            {"cypher": query, "params": params, "scope": "local"},
        ),
        (
            "discovery",
            "graph_search",
            {"query": exact_identity, "mode": "hybrid", "top_k": 8},
        ),
        (
            "caller_impact",
            "graph_code",
            {
                "action": "code_context",
                "query": caller_query,
                "target": "usage",
                "top_k": 8,
            },
        ),
    )


def _architecture_payload_rows(payload: Any, key: str, limit: int) -> list[Any]:
    """Read one exact bounded row collection and reject text-shaped success."""

    if not isinstance(payload, dict) or key not in payload:
        raise ValueError("architecture_response_schema_invalid")
    rows = payload[key]
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("architecture_response_rows_invalid")
    return rows


def _architecture_registry_row(
    candidate: ArchitectureCandidateIdentity, payload: Any
) -> ArchitectureRegistryRow:
    """Return the unique authoritative joined row for the exact candidate."""

    rows = _architecture_payload_rows(payload, "rows", 2)
    if not rows:
        raise ValueError("architecture_registry_unavailable")
    if len(rows) != 1:
        raise ValueError("architecture_registry_duplicate_authority")
    try:
        row = ArchitectureRegistryRow.model_validate(rows[0])
    except Exception as exc:
        raise ValueError("architecture_registry_row_invalid") from exc
    _verify_architecture_registry_identity(candidate, row)
    _verify_architecture_registry_ownership(candidate, row)
    _verify_architecture_registry_replacement(candidate, row)
    return row


def _architecture_registry_expected_identity(
    candidate: ArchitectureCandidateIdentity,
) -> dict[str, str]:
    """Return the exact owner-manifest and signature identity to compare."""

    return {
        "component_id": candidate.component_id,
        "component_kind": candidate.component_kind,
        "capability_id": candidate.capability_id,
        "implementation_authority_component_id": candidate.component_id,
        "parent_component_id": candidate.parent_component_id,
        "parent_layer": candidate.parent_layer,
        "source_authority": _ARCHITECTURE_SOURCE_AUTHORITY,
        "authority_state": _ARCHITECTURE_AUTHORITY_STATE,
        "status": _ARCHITECTURE_ACTIVE_STATUS,
        "source_workspace_manifest": candidate.source_workspace_manifest,
        "source_repository_id": candidate.source_repository_id,
        "source_repository_path": candidate.source_repository_path,
        "source_manifest_path": _ARCHITECTURE_MANIFEST_PATH,
        "source_revision": candidate.source_revision,
        "source_digest": candidate.source_digest,
        "authority_signature": candidate.authority_signature,
        "behavioral_signature": candidate.behavioral_signature,
        "dependency_signature": candidate.dependency_signature,
        "identity_policy_digest": candidate.identity_policy_digest,
    }


def _verify_architecture_registry_identity(
    candidate: ArchitectureCandidateIdentity, row: ArchitectureRegistryRow
) -> None:
    """Reject stale/proposal state separately from source identity disagreement."""

    expected = _architecture_registry_expected_identity(candidate)
    observed = row.model_dump(mode="json")
    disagreements = {
        field_name
        for field_name, value in expected.items()
        if observed[field_name] != value
    }
    if not disagreements:
        return
    stale_fields = {
        "status",
        "source_revision",
        "source_authority",
        "authority_state",
    }
    if disagreements.intersection(stale_fields):
        raise ValueError("architecture_registry_outdated")
    raise ValueError("architecture_owner_manifest_identity_disagreement")


def _verify_architecture_registry_ownership(
    candidate: ArchitectureCandidateIdentity, row: ArchitectureRegistryRow
) -> None:
    """Verify the sole target-inventory link and every finite owner root."""

    if row.target_item_refs != (candidate.target_inventory_ref,):
        raise ValueError("architecture_target_inventory_link_invalid")
    expected = (
        candidate.owned_source_roots,
        candidate.public_contract_roots,
        candidate.test_roots,
        candidate.generated_roots,
        candidate.shared_paths,
    )
    observed = (
        row.owned_source_roots,
        row.public_contract_roots,
        row.test_roots,
        row.generated_roots,
        row.shared_paths,
    )
    if observed != expected:
        raise ValueError("architecture_owner_roots_invalid")


def _verify_architecture_registry_replacement(
    candidate: ArchitectureCandidateIdentity, row: ArchitectureRegistryRow
) -> None:
    """Require exact replacement identities and deletion proof when applicable."""

    if row.replaces != candidate.replaced_component_ids:
        raise ValueError("architecture_replacement_identity_invalid")
    if candidate.replacement_required:
        if row.deletion_proof is None or _DIGEST.fullmatch(row.deletion_proof) is None:
            raise ValueError("architecture_deletion_proof_missing")
        return
    if row.deletion_proof is not None:
        raise ValueError("architecture_unrelated_deletion_proof")


def _architecture_discovery_rows(
    candidate: ArchitectureCandidateIdentity, payload: Any
) -> tuple[ArchitectureDiscoveryRow, ...]:
    """Validate the bounded flat-text ``graph_search`` discovery contract."""

    text = _architecture_discovery_text(payload)
    records = text.split("\n---\n")
    if len(records) != 1:
        raise ValueError("architecture_discovery_unrelated_or_duplicate")
    row_data = _architecture_discovery_record(records[0])
    try:
        row = ArchitectureDiscoveryRow.model_validate(row_data)
    except Exception as exc:
        raise ValueError("architecture_discovery_row_invalid") from exc
    if (
        row.component_id != candidate.component_id
        or row.capability_id != candidate.capability_id
        or row.source_repository_id != candidate.source_repository_id
        or row.source_manifest_path != _ARCHITECTURE_MANIFEST_PATH
        or row.source_digest != candidate.source_digest
        or row.authority_signature != candidate.authority_signature
    ):
        raise ValueError("architecture_discovery_unrelated_or_duplicate")
    return (row,)


def _architecture_discovery_text(payload: Any) -> str:
    """Unwrap only the actual string result shape emitted by ``graph_search``."""

    if isinstance(payload, dict):
        if set(payload) != {"result"} or not isinstance(payload["result"], str):
            raise ValueError("architecture_response_schema_invalid")
        payload = payload["result"]
    if not isinstance(payload, str):
        raise ValueError("architecture_response_schema_invalid")
    text = payload.strip()
    if not text or text.startswith("No results found for query:"):
        raise ValueError("architecture_discovery_unavailable")
    trailer_separator = f"\n\n{_ARCHITECTURE_DISCOVERY_TRAILER}"
    if text.endswith(trailer_separator):
        text = text[: -len(trailer_separator)].rstrip()
    if not text:
        raise ValueError("architecture_discovery_contract_invalid")
    return text


def _architecture_discovery_record(record: str) -> dict[str, str]:
    """Parse one exact Graph-OS formatted component record and its digest."""

    lines = record.splitlines()
    if len(lines) < 2:
        raise ValueError("architecture_discovery_contract_invalid")
    fields = _architecture_discovery_header_fields(lines[0])
    fields.update(_architecture_discovery_body_fields(lines[1:]))
    _validate_architecture_discovery_contract(fields)
    return {
        key: fields[key]
        for key in {"component_id", *_ARCHITECTURE_DISCOVERY_FIELDS}
        if key != "contract_digest"
    }


def _architecture_discovery_header_fields(header_line: str) -> dict[str, str]:
    """Parse the real graph-search result header and bound its score."""

    header = _ARCHITECTURE_DISCOVERY_HEADER.fullmatch(header_line)
    if header is None:
        raise ValueError("architecture_discovery_contract_invalid")
    score = float(header.group("score"))
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("architecture_discovery_contract_invalid")
    if header.group("name") != header.group("component_id"):
        raise ValueError("architecture_discovery_unrelated_or_duplicate")
    return {"component_id": header.group("component_id")}


def _architecture_discovery_body_fields(lines: list[str]) -> dict[str, str]:
    """Parse exactly one key/value line per signed discovery field."""

    fields: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key not in _ARCHITECTURE_DISCOVERY_FIELDS or not value:
            raise ValueError("architecture_discovery_contract_invalid")
        if key in fields:
            raise ValueError("architecture_discovery_contract_invalid")
        fields[key] = value
    return fields


def _validate_architecture_discovery_contract(fields: dict[str, str]) -> None:
    """Verify exact field coverage and the signed contract digest."""

    if set(fields) != {"component_id", *_ARCHITECTURE_DISCOVERY_FIELDS}:
        raise ValueError("architecture_discovery_contract_invalid")
    signed_fields = {
        key: fields[key]
        for key in sorted(
            {"component_id", *_ARCHITECTURE_DISCOVERY_FIELDS - {"contract_digest"}}
        )
    }
    if fields["contract_digest"] != _digest_bytes(_canonical_bytes(signed_fields)):
        raise ValueError("architecture_discovery_contract_invalid")


def _path_belongs_to_candidate(
    candidate: ArchitectureCandidateIdentity, value: str
) -> bool:
    roots = (
        *candidate.owned_source_roots,
        *candidate.public_contract_roots,
        *candidate.test_roots,
    )
    return _canonical_owner_path(value) and any(
        value == root or value.startswith(f"{root.rstrip('/')}/") for root in roots
    )


def _architecture_caller_count(
    candidate: ArchitectureCandidateIdentity, payload: Any
) -> int:
    """Require grounded, owner-scoped caller rows from the typed code-context bundle."""

    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("architecture_caller_evidence_unavailable")
    spans = payload.get("evidence_spans")
    trace = payload.get("reasoning_trace")
    if not isinstance(spans, list) or not 1 <= len(spans) <= 32:
        raise ValueError("architecture_caller_evidence_invalid")
    if not isinstance(trace, list) or len(trace) > 64:
        raise ValueError("architecture_caller_evidence_invalid")
    cited = _architecture_citations(candidate, spans)
    callers = _architecture_callers(trace)
    grounded = sum(
        _architecture_caller_grounded(caller, cited) for caller in callers[:32]
    )
    if grounded < 1:
        raise ValueError("architecture_live_caller_missing")
    return grounded


def _architecture_citations(
    candidate: ArchitectureCandidateIdentity, spans: list[Any]
) -> set[tuple[str, int]]:
    """Return only bounded owner-scoped file/line citations."""

    return {
        (str(span.get("file") or ""), int(span.get("line") or 0))
        for span in spans
        if isinstance(span, dict)
        and isinstance(span.get("line"), int)
        and _path_belongs_to_candidate(candidate, str(span.get("file") or ""))
    }


def _architecture_callers(trace: list[Any]) -> list[Any]:
    """Extract caller rows only from the typed code-context sections step."""

    callers: list[Any] = []
    for step in trace:
        if not isinstance(step, dict) or step.get("step") != "sections":
            continue
        sections = step.get("sections")
        if isinstance(sections, dict) and isinstance(sections.get("callers"), list):
            callers.extend(sections["callers"])
    return callers


def _architecture_caller_grounded(caller: Any, citations: set[tuple[str, int]]) -> bool:
    """Return whether one caller has an identical retained citation."""

    if not isinstance(caller, dict) or not isinstance(caller.get("line"), int):
        return False
    citation = (str(caller.get("file") or ""), int(caller["line"]))
    return citation in citations


def _architecture_error_code(exc: Exception) -> str:
    code = str(exc)
    return code if _SAFE_ERROR.fullmatch(code) else "architecture_response_invalid"


def _architecture_scenario_observations(
    _candidate: ArchitectureCandidateIdentity, errors: set[str]
) -> tuple[ArchitectureScenarioObservation, ...]:
    """Project actual gate results into the deterministic RF-021 scenario schema."""

    registry_missing = "architecture_registry_unavailable" in errors
    registry_stale = bool(
        errors
        & {
            "architecture_registry_outdated",
            "architecture_registry_duplicate_authority",
        }
    )
    identity_mismatch = "architecture_owner_manifest_identity_disagreement" in errors
    refresh_required = bool(errors)
    rejected = bool(errors)
    outcomes = {
        "registry_unavailable": _scenario_outcome(
            registry_missing, "fail_closed", "available"
        ),
        "registry_outdated": _scenario_outcome(registry_stale, "rejected", "current"),
        "owner_manifest_identity_disagreement": _scenario_outcome(
            identity_mismatch, "rejected", "matched"
        ),
        "regeneration_reingestion": _scenario_outcome(
            refresh_required, "rf021_handoff", "not_required"
        ),
        "finite_exception_metadata": _scenario_outcome(
            rejected, "rejected", "verified"
        ),
        "caller_deletion_evidence": _scenario_outcome(rejected, "rejected", "verified"),
        "concept_discovery_only": "discovery_only",
        "plans_cutover": _scenario_outcome(rejected, "rejected", "authoritative"),
        "layer_boundary_vs_component": _scenario_outcome(
            rejected, "rejected", "implementation_component"
        ),
        "parent_layer_no_signature_match": _scenario_outcome(
            rejected, "rejected", "verified"
        ),
        "component_owned_roots": _scenario_outcome(rejected, "rejected", "verified"),
        "worker_lane_shared_file_exception": _scenario_outcome(
            rejected, "rejected", "verified"
        ),
    }
    return tuple(
        ArchitectureScenarioObservation(scenario, outcomes[scenario])
        for scenario in architecture_workflow_scenarios()
    )


def _scenario_outcome(condition: bool, when_true: str, when_false: str) -> str:
    """Select one controlled scenario result without embedding scenario logic."""

    return when_true if condition else when_false


async def _capture_architecture_operations(
    case: ValidationCase,
    result: CaseResult,
    *,
    client: Any,
    timeout: float,
) -> tuple[GraphOperationObservation, ...]:
    """Invoke and capture every RF-021 Graph-OS operation for the dev skill.

    Raw response bodies never reach ``CaseResult`` or a persisted report. Exact
    request/response digests, typed status, and bounded match counts are retained.
    All three calls are attempted so an earlier failure cannot hide another one.
    """

    if case.skill != _ARCHITECTURE_SKILL:
        return ()

    candidate = case.architecture_candidate
    if candidate is None:
        result.add_error("architecture_candidate_identity_missing")
        return ()

    if not _architecture_routes_valid(case):
        result.add_error("architecture_operation_contract_invalid")
        result.operation_evidence = ()
        return ()

    specs = _architecture_operation_specs(candidate)
    budget = min(_ARCHITECTURE_OPERATION_TIMEOUT_SECONDS, max(1.0, timeout))
    deadline = time.monotonic() + budget
    observations, payloads = await _invoke_architecture_operations(
        client, specs, deadline
    )
    counts, validation_errors = _validate_architecture_payloads(candidate, payloads)
    captured = _finalize_architecture_observations(observations, counts)

    result.operation_evidence = captured
    result.scenario_evidence = _architecture_scenario_observations(
        candidate, validation_errors
    )
    for code in sorted(validation_errors):
        result.add_error(code)
    if validation_errors:
        result.add_error("architecture_registry_regenerate_reingest_required")
    return captured


def _architecture_routes_valid(case: ValidationCase) -> bool:
    """Require every real operation to be part of the case route contract."""

    required = {operation for _phase, operation in _ARCHITECTURE_PHASE_OPERATIONS}
    return required.issubset(case.expected_routes)


async def _invoke_architecture_operations(
    client: Any,
    specs: tuple[tuple[str, str, dict[str, Any]], ...],
    deadline: float,
) -> tuple[list[GraphOperationObservation], dict[str, Any]]:
    """Invoke all candidate-bound operations and retain bounded observations."""

    observations: list[GraphOperationObservation] = []
    payloads: dict[str, Any] = {}
    for phase, operation, arguments in specs:
        observation, payload = await _invoke_architecture_operation(
            client, phase, operation, arguments, deadline
        )
        observations.append(observation)
        if payload is not None:
            payloads[phase] = payload
    return observations, payloads


async def _invoke_architecture_operation(
    client: Any,
    phase: str,
    operation: str,
    arguments: dict[str, Any],
    deadline: float,
) -> tuple[GraphOperationObservation, Any | None]:
    """Invoke one operation within the shared deadline and digest its response."""

    request_digest = _digest_bytes(_canonical_bytes(arguments))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _architecture_failed_observation(
            phase, operation, request_digest, "timeout"
        ), None
    try:
        payload = await _call_tool(client, operation, arguments, remaining)
    except TimeoutError:
        return _architecture_failed_observation(
            phase, operation, request_digest, "timeout"
        ), None
    except Exception:  # noqa: BLE001 - retain only typed status evidence
        return _architecture_failed_observation(
            phase, operation, request_digest, "error"
        ), None
    return (
        GraphOperationObservation(
            phase,
            operation,
            "observed",
            request_digest,
            _digest_bytes(_canonical_bytes(payload)),
            0,
        ),
        payload,
    )


def _architecture_failed_observation(
    phase: str, operation: str, request_digest: str, status: str
) -> GraphOperationObservation:
    """Build one content-free timeout or tool-error observation."""

    return GraphOperationObservation(
        phase,
        operation,
        status,
        request_digest,
        _digest_bytes(_canonical_bytes({"status": status})),
        0,
    )


def _architecture_registry_count(
    candidate: ArchitectureCandidateIdentity, payload: Any
) -> int:
    _architecture_registry_row(candidate, payload)
    return 1


def _architecture_discovery_count(
    candidate: ArchitectureCandidateIdentity, payload: Any
) -> int:
    """Return the number of exact advisory discovery rows."""

    return len(_architecture_discovery_rows(candidate, payload))


def _validate_architecture_payloads(
    candidate: ArchitectureCandidateIdentity, payloads: dict[str, Any]
) -> tuple[dict[str, int], set[str]]:
    """Run the three typed validators and return controlled errors only."""

    validators = {
        "registry_lookup": _architecture_registry_count,
        "discovery": _architecture_discovery_count,
        "caller_impact": _architecture_caller_count,
    }
    counts: dict[str, int] = {}
    errors: set[str] = set()
    for phase, validator in validators.items():
        if phase not in payloads:
            errors.add("architecture_operation_unavailable")
            continue
        try:
            counts[phase] = validator(candidate, payloads[phase])
        except Exception as exc:  # noqa: BLE001 - convert to controlled code
            errors.add(_architecture_error_code(exc))
    return counts, errors


def _architecture_verified_status(phase: str) -> str:
    """Return the evidence role for one validated operation phase."""

    return {
        "registry_lookup": "verified",
        "discovery": "advisory",
        "caller_impact": "grounded",
    }[phase]


def _finalize_architecture_observations(
    observations: list[GraphOperationObservation], counts: dict[str, int]
) -> tuple[GraphOperationObservation, ...]:
    """Attach typed validation roles and record counts to tool evidence."""

    return tuple(
        GraphOperationObservation(
            observation.phase,
            observation.operation,
            _architecture_verified_status(observation.phase)
            if observation.phase in counts
            else observation.status,
            observation.request_digest,
            observation.response_digest,
            counts.get(observation.phase, 0),
        )
        for observation in observations
    )
