"""GraphOS bundled-skill runtime core logic."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from graph_os.deployment.skills import BUNDLED_SKILLS
from graph_os.deployment.skills.validation import (
    _ARCHITECTURE_COMPONENT_ID,
    _ARCHITECTURE_MANIFEST_PATH,
    _ARCHITECTURE_SOURCE_REPOSITORY_ID,
    _ARCHITECTURE_SOURCE_REPOSITORY_PATH,
    _ARCHITECTURE_SOURCE_REVISION,
    _ARCHITECTURE_SOURCE_WORKSPACE_MANIFEST,
    _ARCHITECTURE_TARGET_REF,
    FORWARD_MATRIX,
    _architecture_canonical_relative_path,
    _architecture_revision_exists,
)

_SAFE_ROUTE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_MAX_TOOL_PAYLOAD = 64 * 1024
_MAX_TOOL_ITEMS = 4_096
_MAX_TOOL_DEPTH = 24
# Sentinel for "this decoder produced no value", distinct from any JSON value.
_UNDECODED = object()
_ARCHITECTURE_SKILL = "agent-utilities-development"
_ARCHITECTURE_OPERATION_TIMEOUT_SECONDS = 15.0
# RF-021 keeps the proposal registry, owner manifests, and generated projection
# as one contract.  These are scenario labels in the synthetic matrix, not a
# second persisted registry schema.
_ARCHITECTURE_WORKFLOW_SCENARIOS = (
    "registry_unavailable",
    "registry_outdated",
    "owner_manifest_identity_disagreement",
    "regeneration_reingestion",
    "finite_exception_metadata",
    "caller_deletion_evidence",
    "concept_discovery_only",
    "plans_cutover",
)
_ARCHITECTURE_LAYOUT_REQUIREMENTS = (
    "layer_boundary_vs_component",
    "parent_layer_no_signature_match",
    "component_owned_roots",
    "worker_lane_shared_file_exception",
)
_ARCHITECTURE_PASS_OUTCOMES = (
    "available",
    "current",
    "matched",
    "not_required",
    "verified",
    "verified",
    "discovery_only",
    "authoritative",
    "implementation_component",
    "verified",
    "verified",
    "verified",
)
# Conceptual phases deliberately map to the existing Graph-OS operation names.
# Local generation, tests, and deletion remain RF-021 evidence obligations; no
# unsupported Graph-OS verb is invented for them.
_ARCHITECTURE_PHASE_OPERATIONS = (
    ("registry_lookup", "graph_query"),
    ("discovery", "graph_search"),
    ("caller_impact", "graph_code"),
)
_ARCHITECTURE_DISCOVERY_HEADER = re.compile(
    r"^\[ArchitectureComponent\] (?P<name>[^\s]+) "
    r"\(ID: (?P<component_id>[^)]+)\) - Score: (?P<score>[0-9]+(?:\.[0-9]+)?)$"
)
_ARCHITECTURE_DISCOVERY_FIELDS = frozenset(
    {
        "capability_id",
        "source_repository_id",
        "source_manifest_path",
        "source_digest",
        "authority_signature",
        "contract_digest",
    }
)
_ARCHITECTURE_DISCOVERY_TRAILER = "[connection=default graph=(default)]"
_TRACE_PAGE_LIMIT = 20
_TRACE_MAX_PAGES = 10
_TRACE_TOOL_ERROR_RETRIES = 2
_TRACE_TOOL_ERROR_RETRY_DELAY_SECONDS = 0.25
_PARENT_INGESTION_POLL_DELAY_SECONDS = 0.25
_DIRECT_MAX_OUTPUT_TOKENS = 1024
_MAX_REPORT_BYTES = 1_000_000
_SKILL_COUNT = len(BUNDLED_SKILLS)
_CASE_COUNT = _SKILL_COUNT * 2
_PASS = "pass"
_FAIL = "fail"
_NA = "not-applicable"
_DIRECT_CASE_LOCK = asyncio.Lock()
_SYNC_CALL_ACTIVE = threading.Lock()
_SYNC_CALL_POISONED = threading.Event()
_AUTHORITY_TRACE_PRECHECK_CAP_SECONDS = 15.0
_AUTHORITY_EXPORT_FLUSH_CAP_SECONDS = 30.0
_AUTHORITY_PARENT_INGESTION_CAP_SECONDS = 15.0
_AUTHORITY_LEASE_SAFETY_SECONDS = 5.0
_AUTHORITY_RENEWAL_TIMEOUT_SECONDS = 30.0
_DIGEST = re.compile(r"^sha256:(?!0{64}$)[a-f0-9]{64}$")
_RELEASE_ID = re.compile(r"^release-[a-z0-9][a-z0-9.-]{2,63}$")
_COMMAND_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SIGNATURE_ALGORITHMS = frozenset({"ed25519", "ecdsa-p256-sha256", "rsa-pss-sha256"})
_SIGNATURE_VALUE = re.compile(r"^[A-Za-z0-9_-]{43,4096}$")
_KEY_ID = re.compile(r"^key:[a-f0-9]{64}$")
_SIGNER_COMMAND_REFERENCE = "SKILL_VALIDATION_EVIDENCE_SIGNER_COMMAND"
_VERIFIER_COMMAND_REFERENCE = "SKILL_VALIDATION_EVIDENCE_VERIFIER_COMMAND"
_MAX_EXTERNAL_OUTPUT_BYTES = 64 * 1024
_SHELL_EXECUTABLES = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "dash",
        "fish",
        "ksh",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "zsh",
    }
)
_CASE_REFERENCE_PATTERNS = {
    "run": re.compile(r"^pref_run_[a-f0-9]{64}$"),
    "trace": re.compile(r"^pref_trace_[a-f0-9]{64}$"),
    "model": re.compile(r"^pref_model_[a-f0-9]{64}$"),
    "skill": re.compile(r"^pref_skill_[a-f0-9]{64}$"),
    "skill_body": re.compile(r"^pref_skill_body_[a-f0-9]{64}$"),
}


class SemanticOutput(BaseModel):
    """Closed response contract used by both execution paths."""

    skill: str = Field(min_length=1, max_length=64)
    mode: Literal["direct", "delegated"]
    selected_routes: list[str] = Field(min_length=1, max_length=16)
    read_only: bool
    privacy_safe: bool
    acceptance_summary: str = Field(
        min_length=1,
        max_length=1_000,
        description=(
            "One short sentence confirming the bounded synthetic validation; "
            "do not reproduce the requested plan or enumerate its steps."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class ArchitectureSharedPath(BaseModel):
    """One finite RF-021 shared-path exception supplied by the owner manifest."""

    path: str = Field(min_length=1, max_length=256)
    kind: Literal["shared_root", "shared_file"]
    owner_component_ids: tuple[str, ...] = Field(min_length=2, max_length=8)
    review_policy: str = Field(min_length=1, max_length=128)
    exception_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")

    model_config = ConfigDict(extra="forbid", frozen=True)


class ArchitectureCandidateIdentity(BaseModel):
    """Exact, digest-bound RF-021 candidate supplied to runtime validation."""

    component_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    component_kind: Literal["implementation_component"]
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    parent_component_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    parent_layer: str = Field(pattern=r"^[a-z][a-z0-9.-]{1,63}$")
    source_workspace_manifest: Literal["workspace.yml"]
    source_repository_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,127}$")
    source_repository_path: str = Field(min_length=1, max_length=256)
    source_manifest_path: Literal["architecture/component-registry.yml"]
    source_revision: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    source_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    authority_signature: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    behavioral_signature: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    dependency_signature: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    identity_policy_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    target_inventory_ref: str = Field(min_length=1, max_length=256)
    owned_source_roots: tuple[str, ...] = Field(min_length=1, max_length=32)
    public_contract_roots: tuple[str, ...] = Field(min_length=1, max_length=32)
    test_roots: tuple[str, ...] = Field(min_length=1, max_length=32)
    generated_roots: tuple[str, ...] = Field(max_length=32)
    shared_paths: tuple[ArchitectureSharedPath, ...] = Field(max_length=16)
    replacement_required: bool
    replaced_component_ids: tuple[str, ...] = Field(max_length=16)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ArchitectureRegistryRow(BaseModel):
    """Closed joined ArchitectureComponent/ArchitectureCapability observation."""

    component_id: str
    component_kind: str
    capability_id: str
    implementation_authority_component_id: str
    parent_component_id: str
    parent_layer: str
    source_authority: str
    authority_state: str
    status: str
    source_workspace_manifest: str
    source_repository_id: str
    source_repository_path: str
    source_manifest_path: str
    source_revision: str
    source_digest: str
    authority_signature: str
    behavioral_signature: str
    dependency_signature: str
    identity_policy_digest: str
    target_item_refs: tuple[str, ...]
    owned_source_roots: tuple[str, ...]
    public_contract_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    generated_roots: tuple[str, ...]
    shared_paths: tuple[ArchitectureSharedPath, ...]
    replaces: tuple[str, ...]
    deletion_proof: str | None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ArchitectureDiscoveryRow(BaseModel):
    """Closed advisory discovery row; it can never establish ownership."""

    component_id: str
    capability_id: str
    source_repository_id: str
    source_manifest_path: str
    source_digest: str
    authority_signature: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class DelegationContractError(ValueError):
    """Controlled current-contract diagnostic with no response material."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ValidationChildToolError(RuntimeError):
    """Controlled retryable failure returned by an MCP child tool."""


@dataclass(frozen=True)
class GraphOperationObservation:
    """Bounded metadata retained for one real Graph-OS operation invocation."""

    phase: str
    operation: str
    status: str
    request_digest: str
    response_digest: str
    matched_record_count: int


@dataclass(frozen=True)
class ArchitectureScenarioObservation:
    """One deterministic RF-021 behavioral outcome retained as evidence."""

    scenario: str
    outcome: str


@dataclass(frozen=True)
class ValidationCase:
    """One synthetic case loaded from the checked-in matrix."""

    case_id: str
    skill: str
    mode: Literal["direct", "delegated"]
    model_class: Literal["economy", "standard"]
    task: str = field(repr=False)
    expected_routes: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    read_only: bool
    architecture_candidate: ArchitectureCandidateIdentity | None = None


@dataclass
class CaseResult:
    """Privacy-safe evidence retained for one case."""

    case_id: str
    skill: str
    mode: str
    model_class: str
    model_selection: str = _FAIL
    skill_binding: str = _FAIL
    structural: str = _PASS
    semantic: str = _FAIL
    delegation: str = _NA
    trace: str = _FAIL
    parent_ingestion: str = _FAIL
    trace_linkage: str = "none"
    trace_name: str = ""
    langfuse_match_count: int = 0
    parent_kg_readback_count: int = 0
    selected_routes: tuple[str, ...] = ()
    run_ref: str = ""
    trace_ref: str = ""
    model_ref: str = ""
    skill_ref: str = ""
    skill_body_ref: str = ""
    operation_evidence: tuple[GraphOperationObservation, ...] = ()
    scenario_evidence: tuple[ArchitectureScenarioObservation, ...] = ()
    error_codes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(
            self._required_checks_passed()
            and not self.error_codes
            and self._trace_evidence_exact()
            and self.selected_routes
            and all(_SAFE_ROUTE.fullmatch(route) for route in self.selected_routes)
            and self._all_evidence_exact()
        )

    def _required_checks_passed(self) -> bool:
        """Require every mandatory per-mode check to have recorded a pass."""

        required = [
            self.structural,
            self.semantic,
            self.trace,
            self.parent_ingestion,
            self.model_selection,
            self.skill_binding,
        ]
        if self.mode == "delegated":
            required.append(self.delegation)
        return all(value == _PASS for value in required)

    def _trace_evidence_exact(self) -> bool:
        """Require exactly one run-linked trace and one parent-ingested node."""

        return (
            self.trace_linkage == "run-evidence"
            and self.trace_name == f"graph_run:{self.run_ref}"
            and self.langfuse_match_count == 1
            and self.parent_kg_readback_count == 1
        )

    def _references_valid(self) -> bool:
        """Require every retained opaque reference to match its exact pattern."""

        return all(
            pattern.fullmatch(value) is not None
            for pattern, value in (
                (_CASE_REFERENCE_PATTERNS["run"], self.run_ref),
                (_CASE_REFERENCE_PATTERNS["trace"], self.trace_ref),
                (_CASE_REFERENCE_PATTERNS["model"], self.model_ref),
                (_CASE_REFERENCE_PATTERNS["skill"], self.skill_ref),
                (_CASE_REFERENCE_PATTERNS["skill_body"], self.skill_body_ref),
            )
        )

    def _all_evidence_exact(self) -> bool:
        """Require opaque runtime references and the skill-specific evidence."""

        return self._references_valid() and self._architecture_evidence_exact()

    def _architecture_evidence_exact(self) -> bool:
        """Require the exact joined/discovery/caller evidence for dev-skill cases."""

        if self.skill != _ARCHITECTURE_SKILL:
            return not self.operation_evidence and not self.scenario_evidence
        return (
            self._architecture_operations_exact()
            and self._architecture_scenarios_exact()
        )

    def _architecture_operations_exact(self) -> bool:
        """Require the three candidate-bound tool observations in contract order."""

        expected = tuple(
            (phase, operation, status)
            for (phase, operation), status in zip(
                _ARCHITECTURE_PHASE_OPERATIONS,
                ("verified", "advisory", "grounded"),
                strict=True,
            )
        )
        observed = tuple(
            (item.phase, item.operation, item.status)
            for item in self.operation_evidence
        )
        return (
            observed == expected
            and tuple(item.matched_record_count for item in self.operation_evidence[:2])
            == (1, 1)
            and 1 <= self.operation_evidence[2].matched_record_count <= 32
            and all(
                _DIGEST.fullmatch(item.request_digest) is not None
                and _DIGEST.fullmatch(item.response_digest) is not None
                for item in self.operation_evidence
            )
        )

    def _architecture_scenarios_exact(self) -> bool:
        """Require every structured scenario and no failing outcome."""

        expected = tuple(
            ArchitectureScenarioObservation(scenario, outcome)
            for scenario, outcome in zip(
                (_ARCHITECTURE_WORKFLOW_SCENARIOS + _ARCHITECTURE_LAYOUT_REQUIREMENTS),
                _ARCHITECTURE_PASS_OUTCOMES,
                strict=True,
            )
        )
        return self.scenario_evidence == expected

    def add_error(self, code: str) -> None:
        normalized = re.sub(r"[^a-z0-9_]+", "_", code.casefold()).strip("_")
        if not _SAFE_ERROR.fullmatch(normalized):
            normalized = "validation_error"
        if normalized not in self.error_codes:
            self.error_codes.append(normalized)


@dataclass(frozen=True)
class TraceRecord:
    """Metadata-only evidence retained from one exact-name trace lookup."""

    name: str
    evidence: dict[str, str]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_digest(value: str, field: str) -> str:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field}_invalid")
    return value


def _architecture_candidate_ref(candidate: ArchitectureCandidateIdentity) -> str:
    """Return a content-bound reference without persisting candidate identities."""

    digest = hashlib.sha256(
        _canonical_bytes(candidate.model_dump(mode="json"))
    ).hexdigest()
    return f"pref_architecture_candidate_{digest}"


def _canonical_owner_path(value: str) -> bool:
    """Return whether an owner root is finite, relative, and non-patterned."""

    return _canonical_owner_path_checked(value)


def _canonical_owner_path_checked(value: Any) -> bool:
    """Apply the type guard and syntax checks for one owner path."""

    if not isinstance(value, str):
        return False
    path = Path(value)
    return bool(
        value
        and len(value) <= 256
        and not path.is_absolute()
        and value == path.as_posix()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and not any(marker in value for marker in ("*", "?", "[", "]", "\x00"))
    )


def _owner_paths_overlap(left: str, right: str) -> bool:
    """Return whether two canonical owner paths contain one another."""

    return bool(
        left == right
        or left.startswith(f"{right.rstrip('/')}/")
        or right.startswith(f"{left.rstrip('/')}/")
    )


def _validate_architecture_candidate(
    candidate: ArchitectureCandidateIdentity,
) -> ArchitectureCandidateIdentity:
    """Fail closed on a broad, overlapping, or internally inconsistent candidate."""

    _validate_architecture_candidate_digests(candidate)
    roots = _validate_architecture_candidate_roots(candidate)
    _validate_architecture_candidate_identity(candidate)
    _validate_architecture_candidate_shared_paths(candidate, roots)
    return candidate


def _validate_architecture_candidate_digests(
    candidate: ArchitectureCandidateIdentity,
) -> None:
    """Require every externally supplied content identity to be non-sentinel."""

    digest_fields = (
        "source_digest",
        "authority_signature",
        "behavioral_signature",
        "dependency_signature",
        "identity_policy_digest",
    )
    invalid = next(
        (
            field_name
            for field_name in digest_fields
            if _DIGEST.fullmatch(str(getattr(candidate, field_name))) is None
        ),
        None,
    )
    if invalid is not None:
        raise ValueError(f"architecture_candidate_{invalid}_invalid")


def _validate_architecture_candidate_roots(
    candidate: ArchitectureCandidateIdentity,
) -> tuple[str, ...]:
    """Require unique finite roots and isolate generated from handwritten paths."""

    root_groups = (
        candidate.owned_source_roots,
        candidate.public_contract_roots,
        candidate.test_roots,
        candidate.generated_roots,
    )
    _validate_architecture_root_groups(root_groups)
    _validate_generated_root_isolation(root_groups[:3], candidate.generated_roots)
    flattened = [root for group in root_groups for root in group]
    return tuple(flattened)


def _validate_architecture_root_groups(
    root_groups: tuple[tuple[str, ...], ...],
) -> None:
    """Require every owner root to be finite and unique across all roles."""

    for group in root_groups:
        _validate_architecture_root_group(group)
    _validate_architecture_cross_role_roots(root_groups)


def _validate_architecture_root_group(group: tuple[str, ...]) -> None:
    """Require one owner-root role to be finite and internally unique."""

    if any(not _canonical_owner_path(root) for root in group):
        raise ValueError("architecture_candidate_root_invalid")
    if len(group) != len(set(group)):
        raise ValueError("architecture_candidate_root_duplicate")


def _validate_architecture_cross_role_roots(
    root_groups: tuple[tuple[str, ...], ...],
) -> None:
    """Reject identical or nested roots assigned to different roles."""

    for index, left_group in enumerate(root_groups):
        for right_group in root_groups[index + 1 :]:
            if any(
                _owner_paths_overlap(left, right)
                for left in left_group
                for right in right_group
            ):
                raise ValueError("architecture_candidate_root_overlap")


def _validate_generated_root_isolation(
    handwritten_groups: tuple[tuple[str, ...], ...], generated_roots: tuple[str, ...]
) -> None:
    """Keep generated ownership disjoint from handwritten owner roots."""

    if any(
        _owner_paths_overlap(handwritten_root, generated_root)
        for group in handwritten_groups
        for handwritten_root in group
        for generated_root in generated_roots
    ):
        raise ValueError("architecture_candidate_generated_root_overlap")


def _validate_architecture_candidate_identity(
    candidate: ArchitectureCandidateIdentity,
) -> None:
    """Require canonical parent, manifest, and replacement identity semantics."""

    if candidate.parent_component_id == candidate.component_id:
        raise ValueError("architecture_candidate_parent_invalid")
    _validate_architecture_candidate_owner_source(candidate)
    if candidate.source_manifest_path != _ARCHITECTURE_MANIFEST_PATH:
        raise ValueError("architecture_candidate_manifest_path_invalid")
    _validate_architecture_candidate_source_identity(candidate)
    if candidate.replacement_required != bool(candidate.replaced_component_ids):
        raise ValueError("architecture_candidate_replacement_contract_invalid")


def _validate_architecture_candidate_owner_source(
    candidate: ArchitectureCandidateIdentity,
) -> None:
    """Require the exact AU source identity before any live operation."""

    if candidate.source_workspace_manifest != _ARCHITECTURE_SOURCE_WORKSPACE_MANIFEST:
        raise ValueError("architecture_candidate_source_workspace_invalid")
    if candidate.source_repository_id != _ARCHITECTURE_SOURCE_REPOSITORY_ID:
        raise ValueError("architecture_candidate_source_repository_invalid")
    if candidate.source_repository_path != _ARCHITECTURE_SOURCE_REPOSITORY_PATH:
        raise ValueError("architecture_candidate_source_repository_path_invalid")


def _validate_architecture_candidate_source_identity(
    candidate: ArchitectureCandidateIdentity,
) -> None:
    """Require a relative source, real revision, and canonical target reference."""

    if not _architecture_canonical_relative_path(candidate.source_repository_path):
        raise ValueError("architecture_candidate_source_repository_path_invalid")
    if _ARCHITECTURE_SOURCE_REVISION.fullmatch(candidate.source_revision) is None:
        raise ValueError("architecture_candidate_source_revision_invalid")
    if not _architecture_revision_exists(candidate.source_revision):
        raise ValueError("architecture_candidate_source_revision_unavailable")
    if _ARCHITECTURE_TARGET_REF.fullmatch(candidate.target_inventory_ref) is None:
        raise ValueError("architecture_candidate_target_inventory_ref_invalid")


def _validate_architecture_candidate_shared_paths(
    candidate: ArchitectureCandidateIdentity, exclusive_roots: tuple[str, ...]
) -> None:
    """Require complete co-ownership and disjoint shared-path exceptions."""

    for shared in candidate.shared_paths:
        _validate_architecture_shared_path(candidate, shared, exclusive_roots)


def _validate_architecture_shared_path(
    candidate: ArchitectureCandidateIdentity,
    shared: ArchitectureSharedPath,
    exclusive_roots: tuple[str, ...],
) -> None:
    """Validate one finite shared-root or shared-file exception."""

    if not _canonical_owner_path(shared.path):
        raise ValueError("architecture_candidate_shared_path_invalid")
    if candidate.component_id not in shared.owner_component_ids:
        raise ValueError("architecture_candidate_shared_owner_missing")
    if len(shared.owner_component_ids) != len(set(shared.owner_component_ids)):
        raise ValueError("architecture_candidate_shared_owner_duplicate")
    _validate_architecture_shared_owner_ids(shared.owner_component_ids)
    if any(_owner_paths_overlap(shared.path, root) for root in exclusive_roots):
        raise ValueError("architecture_candidate_shared_exclusive_overlap")


def _validate_architecture_shared_owner_ids(owner_ids: tuple[str, ...]) -> None:
    """Require every shared-path owner to use the canonical component ID shape."""

    if any(
        not isinstance(owner_id, str)
        or _ARCHITECTURE_COMPONENT_ID.fullmatch(owner_id) is None
        for owner_id in owner_ids
    ):
        raise ValueError("architecture_candidate_shared_owner_invalid")


def _case_contract(case: ValidationCase) -> dict[str, Any]:
    """Return the content-free canonical contract bound into release evidence."""

    contract = {
        "id": case.case_id,
        "skill": case.skill,
        "mode": case.mode,
        "modelClass": case.model_class,
        "taskDigest": _digest_bytes(case.task.encode("utf-8")),
        "expectedRoutes": list(case.expected_routes),
        "allowedTools": list(case.allowed_tools),
        "readOnly": case.read_only,
    }
    contract.update(_architecture_contract_binding(case))
    return contract


def _architecture_contract_binding(case: ValidationCase) -> dict[str, str]:
    """Bind development cases to a candidate without changing other contracts."""

    if case.architecture_candidate is None:
        return {}
    return {
        "architectureCandidateRef": _architecture_candidate_ref(
            case.architecture_candidate
        )
    }


def _test_catalog_evidence(cases: list[ValidationCase]) -> dict[str, Any]:
    matrix = yaml.safe_load(FORWARD_MATRIX.read_text(encoding="utf-8"))
    contracts = [_case_contract(case) for case in cases]
    if (
        len(contracts) != _CASE_COUNT
        or len({item["id"] for item in contracts}) != _CASE_COUNT
    ):
        raise RuntimeError("test_catalog_not_exact")
    case_digests = {
        item["id"]: _digest_bytes(_canonical_bytes(item)) for item in contracts
    }
    return {
        "testCatalogDigest": _digest_bytes(_canonical_bytes(matrix)),
        "caseCatalogDigest": _digest_bytes(
            _canonical_bytes(
                [
                    {"caseId": case_id, "caseDigest": case_digests[case_id]}
                    for case_id in sorted(case_digests)
                ]
            )
        ),
        "caseDigests": case_digests,
    }
