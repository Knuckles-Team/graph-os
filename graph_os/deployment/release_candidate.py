"""Trusted immutable candidate consumption and dependency planning (no apply)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9.-]{0,63}$")]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Contract = Annotated[str, Field(pattern=r"^[a-zA-Z0-9._/-]{1,128}$")]
Repository = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
_LIMIT = 4 * 1024 * 1024


class CandidateError(ValueError):
    """A stable privacy-safe refusal; never includes raw input or paths."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Artifact(_Closed):
    component_id: Identifier
    repository: Repository
    source_revision: Revision
    digest: Digest
    kind: Literal["image", "wheel"]
    api: Contract
    schema_contract: Contract = Field(alias="schema")
    build_receipt: str


class Predecessor(_Closed):
    component_id: Identifier
    api: Contract
    schema_contract: Contract = Field(alias="schema")


class Stage(_Closed):
    component_id: Identifier
    optional: bool
    enabled: bool
    predecessors: tuple[Predecessor, ...] = Field(max_length=256)
    probe_contract: Contract


class Profile(_Closed):
    component_id: Literal["graph-os"]
    digest: Digest
    artifact_digest: Digest


class Candidate(_Closed):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    candidate_id: Identifier
    created_at: str = Field(max_length=64)
    artifacts: tuple[Artifact, ...] = Field(min_length=1, max_length=256)
    profiles: tuple[Profile, ...] = Field(min_length=1, max_length=1)
    stages: tuple[Stage, ...] = Field(min_length=1, max_length=256)


ObligationId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")]
TestReference = Annotated[str, Field(pattern=r"^[A-Za-z0-9_./:-]{1,256}$")]


class ExitCriteriaError(ValueError):
    """A stable refusal for an invalid exit-criteria matrix; never echoes input."""


class ExitCriterionRow(_Closed):
    """One release-readiness obligation mapped to the test that proves it."""

    obligation_id: ObligationId
    description: str = Field(min_length=1, max_length=256)
    test_reference: TestReference


class ExitCriteriaMatrix(_Closed):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    rows: tuple[ExitCriterionRow, ...] = Field(min_length=1, max_length=64)


def read_exit_criteria_matrix(raw_rows: list[dict[str, Any]]) -> ExitCriteriaMatrix:
    """Validate and index a candidate exit-criteria matrix (GRAPHOS-RELEASE-R003.1).

    Refuses a duplicate obligation ID and an empty matrix. ``ExitCriterionRow``'s
    own field patterns refuse a malformed test reference. This is the typed-model
    slice only: loading a real matrix from a committed fixture or CLI entry point
    is GRAPHOS-RELEASE-R003.2 onward (tasks.md).
    """
    if not raw_rows:
        raise ExitCriteriaError("exit_criteria_empty")
    seen: dict[str, ExitCriterionRow] = {}
    for raw in raw_rows:
        try:
            row = ExitCriterionRow.model_validate(raw)
        except ValidationError as exc:
            raise ExitCriteriaError("exit_criteria_row_invalid") from exc
        if row.obligation_id in seen:
            raise ExitCriteriaError("exit_criteria_duplicate_obligation")
        seen[row.obligation_id] = row
    return ExitCriteriaMatrix(schema_version=1, rows=tuple(seen.values()))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CandidateError("candidate_duplicate_key")
        result[key] = value
    return result


def _read_candidate(path: Path) -> Any:
    try:
        with os.fdopen(
            os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb"
        ) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CandidateError("candidate_input_invalid")
            data = stream.read(_LIMIT + 1)
        if len(data) > _LIMIT:
            raise CandidateError("candidate_input_too_large")
        return json.loads(data, object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CandidateError("candidate_input_invalid") from exc


def _canonical_digest(raw: Any) -> str:
    try:
        canonical = json.dumps(
            raw,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (ValueError, RecursionError) as exc:
        raise CandidateError("candidate_input_invalid") from exc
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _public_receipt(url: str, repository: str, revision: str) -> bool:
    # An exact GitHub commit/check URL is the only public evidence projection.
    # Queries, fragments, credentials, arbitrary hosts and endpoints are refused.
    prefix = f"https://github.com/{repository}/"
    paths = (f"commit/{revision}", f"commit/{revision}/checks")
    if url in tuple(prefix + path for path in paths):
        return True
    return re.fullmatch(re.escape(prefix) + r"actions/runs/[0-9]+", url) is not None


def _artifact_index(candidate: Candidate) -> dict[str, Artifact]:
    index = {item.component_id: item for item in candidate.artifacts}
    if len(index) != len(candidate.artifacts):
        raise CandidateError("candidate_duplicate_component")
    for item in index.values():
        if not _public_receipt(
            item.build_receipt, item.repository, item.source_revision
        ):
            raise CandidateError("candidate_evidence_invalid")
    return index


def _stage_index(candidate: Candidate) -> dict[str, Stage]:
    stages = {stage.component_id: stage for stage in candidate.stages}
    if len(stages) != len(candidate.stages):
        raise CandidateError("candidate_duplicate_stage")
    for stage in stages.values():
        if not stage.enabled and not stage.optional:
            raise CandidateError("candidate_required_stage_disabled")
        edges = [edge.component_id for edge in stage.predecessors]
        if len(edges) != len(set(edges)):
            raise CandidateError("candidate_duplicate_edge")
    return stages


def _dependency_graph(
    stages: dict[str, Stage], artifacts: dict[str, Artifact]
) -> dict[str, set[str]]:
    if set(stages) != set(artifacts):
        raise CandidateError("candidate_unknown_component")
    enabled = {key: stage for key, stage in stages.items() if stage.enabled}
    graph: dict[str, set[str]] = {}
    for key, stage in enabled.items():
        graph[key] = set()
        for edge in stage.predecessors:
            if edge.component_id not in enabled:
                raise CandidateError(
                    f"candidate_missing_predecessor:{key}:{edge.component_id}"
                )
            artifact = artifacts[edge.component_id]
            if (edge.api, edge.schema_contract) != (
                artifact.api,
                artifact.schema_contract,
            ):
                raise CandidateError(
                    f"candidate_incompatible_edge:{key}:{edge.component_id}"
                )
            graph[key].add(edge.component_id)
    return graph


def _order(graph: dict[str, set[str]]) -> list[str]:
    remaining = {key: set(edges) for key, edges in graph.items()}
    ordered: list[str] = []
    while remaining:
        ready = sorted(key for key, edges in remaining.items() if not edges)
        if not ready:
            raise CandidateError("candidate_cycle:" + ",".join(sorted(remaining)))
        # Recompute after each node so the tie-break is globally lexicographic.
        key = ready[0]
        ordered.append(key)
        del remaining[key]
        for edges in remaining.values():
            edges.discard(key)
    return ordered


def _verify_candidate(raw: Any) -> dict[str, Any]:
    try:
        from agent_utilities.skills.runtime_validation import verify_signed_evidence

        return verify_signed_evidence(
            raw, verifier_reference="CERT_EVIDENCE_VERIFIER_COMMAND"
        )
    except Exception as exc:
        raise CandidateError("candidate_signature_unverified") from exc


def read_candidate(path: Path, *, trusted_digest: str) -> tuple[Candidate, str]:
    """Verify the signed candidate through AU, then enforce the selected digest.

    The hash selects a candidate; it never grants trust. AU's configured verifier
    authenticates the entire unsigned document before any deployment input is
    consumed. Doctor's signed release/compatibility authority stays unchanged.
    """
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", trusted_digest):
        raise CandidateError("candidate_trust_required")
    raw = _verify_candidate(_read_candidate(path))
    digest = _canonical_digest(raw)
    if not hmac.compare_digest(digest, trusted_digest):
        raise CandidateError("candidate_trust_mismatch")
    try:
        candidate = Candidate.model_validate_json(json.dumps(raw, allow_nan=False))
        created_at = datetime.fromisoformat(candidate.created_at)
        if created_at.utcoffset() is None:
            raise ValueError("timezone required")
    except (ValidationError, ValueError) as exc:
        raise CandidateError("candidate_schema_invalid") from exc
    return candidate, digest


def plan_candidate(
    path: Path, *, trusted_digest: str, profile_name: str
) -> dict[str, Any]:
    """Validate trust, closure, ordering and the existing profile before planning."""
    from .genesis_environments import EnvironmentProfileError, load_environment_profile

    candidate, digest = read_candidate(path, trusted_digest=trusted_digest)
    artifacts = _artifact_index(candidate)
    stages = _stage_index(candidate)
    graph = _dependency_graph(stages, artifacts)
    ordered = _order(graph)
    binding = candidate.profiles[0]
    if (
        "graph-os" not in graph
        or binding.artifact_digest != artifacts["graph-os"].digest
    ):
        raise CandidateError("candidate_profile_artifact_mismatch")
    try:
        profile = load_environment_profile(profile_name, expected_digest=binding.digest)
    except (EnvironmentProfileError, OSError) as exc:
        raise CandidateError("candidate_profile_invalid") from exc
    if (profile.release.tag_policy, profile.release.revision) != (
        "digest-pinned",
        binding.artifact_digest,
    ):
        raise CandidateError("candidate_profile_artifact_mismatch")
    return {
        "status": "planned",
        "executed": False,
        "acceptance": "not_audited",
        "manifest_digest": digest,
        "profile_digest": binding.digest,
        "created_at": datetime.now(UTC).isoformat(),
        "order": ordered,
        "stages": [
            {
                "component_id": key,
                "digest": artifacts[key].digest,
                "source_revision": artifacts[key].source_revision,
                "predecessors": sorted(graph[key]),
                "build_receipt": artifacts[key].build_receipt,
            }
            for key in ordered
        ],
        "redacted": True,
    }
