"""GraphOS bundled-skill runtime signing logic."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, TypeGuard

from agent_utilities.core._env import setting
from agent_utilities.release_catalogs import prebundled_skill_catalog_digest
from agent_utilities.security.persistence_privacy import PersistencePrivacyGuard

from graph_os.deployment.skills.validation import SKILLS_ROOT

from .runtime_validation_core import (
    _CASE_COUNT,
    _COMMAND_REFERENCE,
    _FAIL,
    _KEY_ID,
    _MAX_EXTERNAL_OUTPUT_BYTES,
    _PASS,
    _RELEASE_ID,
    _SHELL_EXECUTABLES,
    _SIGNATURE_ALGORITHMS,
    _SIGNATURE_VALUE,
    _SKILL_COUNT,
    CaseResult,
    ValidationCase,
    _architecture_candidate_ref,
    _canonical_bytes,
    _digest_bytes,
    _require_digest,
    _test_catalog_evidence,
)
from .runtime_validation_matrix import (
    load_matrix,
)
from .runtime_validation_report import (
    _report_payload,
)


def _valid_command_word(item: object) -> bool:
    """Accept only a bounded, NUL-free, non-empty argv word."""

    return isinstance(item, str) and 0 < len(item) <= 4_096 and "\x00" not in item


def _valid_command_argv(argv: object) -> TypeGuard[list[str]]:
    """Accept only a bounded list of valid argv words."""

    return (
        isinstance(argv, list)
        and 1 <= len(argv) <= 32
        and all(_valid_command_word(item) for item in argv)
    )


def _external_executable_unsafe(
    executable: Path,
    original: os.stat_result,
    canonical: Path,
    metadata: os.stat_result,
) -> bool:
    """Reject a relative, symlinked, swapped, shell, or non-executable target."""

    return (
        not executable.is_absolute()
        or stat.S_ISLNK(original.st_mode)
        or not stat.S_ISREG(original.st_mode)
        or (original.st_dev, original.st_ino) != (metadata.st_dev, metadata.st_ino)
        or canonical.name.casefold() in _SHELL_EXECUTABLES
        or canonical.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(canonical, os.X_OK)
    )


def _validate_external_command_argv(argv: object) -> list[str]:
    """Resolve one bounded, non-shell external command without executing it."""

    if not _valid_command_argv(argv):
        raise RuntimeError("evidence_command_reference_invalid")
    executable = Path(argv[0])
    try:
        original = executable.lstat()
        canonical = executable.resolve(strict=True)
        metadata = canonical.lstat()
    except OSError as exc:
        raise RuntimeError("evidence_command_reference_invalid") from exc
    if _external_executable_unsafe(executable, original, canonical, metadata):
        raise RuntimeError("evidence_command_reference_invalid")
    return [str(canonical), *argv[1:]]


def _external_command(reference: str) -> list[str]:
    if _COMMAND_REFERENCE.fullmatch(reference) is None:
        raise RuntimeError("evidence_command_reference_invalid")
    raw = str(setting(reference, "") or "")
    if not raw:
        raise RuntimeError("evidence_command_reference_unresolved")
    try:
        argv = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("evidence_command_reference_invalid") from exc
    return _validate_external_command_argv(argv)


def _external_json(reference: str, payload: bytes) -> dict[str, Any]:
    completed = subprocess.run(
        _external_command(reference),
        input=payload,
        capture_output=True,
        check=False,
        timeout=120,
        close_fds=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("external_evidence_command_failed")
    if len(completed.stdout) > _MAX_EXTERNAL_OUTPUT_BYTES:
        raise RuntimeError("external_evidence_output_too_large")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("external_evidence_output_invalid") from exc
    if not isinstance(response, dict):
        raise RuntimeError("external_evidence_output_invalid")
    return response


def _signature_from_response(
    response: dict[str, Any], *, subject_digest: str
) -> dict[str, str]:
    signature = {
        "algorithm": str(response.get("algorithm") or ""),
        "keyId": str(response.get("keyId") or ""),
        "signature": str(response.get("signature") or ""),
        "subjectDigest": str(response.get("subjectDigest") or ""),
    }
    if (
        set(response) != set(signature)
        or signature["algorithm"] not in _SIGNATURE_ALGORITHMS
        or _KEY_ID.fullmatch(signature["keyId"]) is None
        or _SIGNATURE_VALUE.fullmatch(signature["signature"]) is None
        or signature["subjectDigest"] != subject_digest
    ):
        raise RuntimeError("evidence_signature_invalid")
    return signature


def sign_and_verify_evidence(
    unsigned: dict[str, Any], *, signer_reference: str, verifier_reference: str
) -> dict[str, Any]:
    """Sign canonical evidence externally and require the independent verifier."""

    if "signature" in unsigned:
        raise RuntimeError("evidence_unsigned_contract_invalid")
    subject_digest = _digest_bytes(_canonical_bytes(unsigned))
    signature = _signature_from_response(
        _external_json(signer_reference, _canonical_bytes(unsigned)),
        subject_digest=subject_digest,
    )
    signed = {**unsigned, "signature": signature}
    verification = _external_json(verifier_reference, _canonical_bytes(signed))
    if verification != {
        "verified": True,
        "subjectDigest": subject_digest,
        "keyId": signature["keyId"],
    }:
        raise RuntimeError("evidence_verification_failed")
    return signed


def verify_signed_evidence(
    signed: dict[str, Any], *, verifier_reference: str
) -> dict[str, Any]:
    """Independently verify one closed evidence document.

    The verifier receives the canonical signed document over stdin and must
    return the exact bounded acknowledgement used by the producer.  This
    function never trusts a producer-side verification result and never emits
    signer, command, path, endpoint, or identity material.
    """

    if not isinstance(signed, dict) or "signature" not in signed:
        raise RuntimeError("evidence_signed_contract_invalid")
    signature_value = signed.get("signature")
    if not isinstance(signature_value, dict):
        raise RuntimeError("evidence_signature_invalid")
    unsigned = {key: value for key, value in signed.items() if key != "signature"}
    subject_digest = _digest_bytes(_canonical_bytes(unsigned))
    signature = _signature_from_response(signature_value, subject_digest=subject_digest)
    verification = _external_json(verifier_reference, _canonical_bytes(signed))
    expected = {
        "verified": True,
        "subjectDigest": subject_digest,
        "keyId": signature["keyId"],
    }
    if verification != expected:
        raise RuntimeError("evidence_verification_failed")
    return unsigned


def _controlled_ref(value: str) -> str | None:
    """Retain an opaque reference only when it matches the exact ref pattern."""

    return value if re.fullmatch(r"pref_[a-z_]+_[a-f0-9]{64}", value or "") else None


def _controlled_trace_name(value: str) -> str | None:
    """Retain a trace name only when it matches the exact opaque run pattern."""

    return (
        value if re.fullmatch(r"graph_run:pref_run_[a-f0-9]{64}", value or "") else None
    )


def _require_exact_case_set(
    results: list[CaseResult],
    result_by_id: dict[str, CaseResult],
    cases: list[ValidationCase],
) -> None:
    """Require exactly one result per catalog case, with no duplicate ids."""

    if (
        len(results) != _CASE_COUNT
        or len(result_by_id) != _CASE_COUNT
        or set(result_by_id) != {case.case_id for case in cases}
    ):
        raise RuntimeError("runtime_case_set_not_exact")


def _evidence_case_entry(
    case: ValidationCase, result: CaseResult, case_digest: str
) -> dict[str, Any]:
    """Build the closed, content-free evidence subject for one case."""

    return {
        "caseId": case.case_id,
        "caseDigest": case_digest,
        "skill": case.skill,
        "mode": case.mode,
        "modelClass": result.model_class,
        "status": _PASS if result.passed else _FAIL,
        "checks": {
            "structural": result.structural,
            "modelSelection": result.model_selection,
            "skillBinding": result.skill_binding,
            "semantic": result.semantic,
            "delegation": result.delegation,
            "trace": result.trace,
            "parentKnowledgeGraph": result.parent_ingestion,
        },
        "skillRef": _controlled_ref(result.skill_ref),
        "skillBodyRef": _controlled_ref(result.skill_body_ref),
        "runRef": _controlled_ref(result.run_ref),
        "traceRef": _controlled_ref(result.trace_ref),
        "langfuse": {
            "lookupMethod": "exact-name",
            "metadataOnly": True,
            "traceName": _controlled_trace_name(result.trace_name),
            "matchCount": result.langfuse_match_count,
            "linkage": result.trace_linkage,
        },
        "parentKnowledgeGraph": {
            "readbackMethod": "exact-trace-name",
            "matchCount": result.parent_kg_readback_count,
        },
        "architecture": _architecture_evidence_entry(case, result),
        "errorCodes": sorted(result.error_codes),
    }


def _architecture_evidence_entry(
    case: ValidationCase, result: CaseResult
) -> dict[str, Any]:
    """Build the exact content-free architecture observation block."""

    candidate_ref = (
        _architecture_candidate_ref(case.architecture_candidate)
        if case.architecture_candidate is not None
        else ""
    )
    return {
        "candidateRef": _controlled_ref(candidate_ref),
        "operations": [
            {
                "phase": item.phase,
                "operation": item.operation,
                "status": item.status,
                "requestDigest": item.request_digest,
                "responseDigest": item.response_digest,
                "matchedRecordCount": item.matched_record_count,
            }
            for item in result.operation_evidence
        ],
        "scenarios": [
            {"scenario": item.scenario, "outcome": item.outcome}
            for item in result.scenario_evidence
        ],
    }


def _fully_passed_skill_count(results: list[CaseResult]) -> int:
    """Count skills whose direct and delegated cases are both present and passed."""

    skills = {result.skill for result in results}
    return sum(
        len(items) == 2 and all(item.passed for item in items)
        for skill in skills
        for items in [[item for item in results if item.skill == skill]]
    )


def _evidence_result_block(passed: int, fully_passed: int) -> dict[str, Any]:
    """Build the aggregate result block of the evidence subject."""

    exact = passed == _CASE_COUNT and fully_passed == _SKILL_COUNT
    return {
        "status": _PASS if exact else _FAIL,
        "passedCases": passed,
        "totalCases": _CASE_COUNT,
        "fullyPassedSkills": fully_passed,
        "totalSkills": _SKILL_COUNT,
    }


def build_evidence(
    results: list[CaseResult],
    *,
    generated_at: str,
    release_id: str,
    release_specification_digest: str,
    promotion_evidence_digest: str,
    graph_os_digest: str,
    engine_digest: str,
    runtime_config_digest: str,
    runtime_profile_digest: str,
    model_registry_digest: str,
) -> dict[str, Any]:
    """Build the closed, content-free exact-release skill evidence subject."""

    if _RELEASE_ID.fullmatch(release_id) is None:
        raise ValueError("release_id_invalid")
    _require_digest(release_specification_digest, "release_specification_digest")
    _require_digest(promotion_evidence_digest, "promotion_evidence_digest")
    _require_digest(graph_os_digest, "graph_os_digest")
    _require_digest(engine_digest, "engine_digest")
    _require_digest(runtime_config_digest, "runtime_config_digest")
    _require_digest(runtime_profile_digest, "runtime_profile_digest")
    _require_digest(model_registry_digest, "model_registry_digest")
    _defaults, cases = load_matrix()
    catalog = _test_catalog_evidence(cases)
    result_by_id = {result.case_id: result for result in results}
    _require_exact_case_set(results, result_by_id, cases)
    evidence_cases = [
        _evidence_case_entry(
            case,
            result_by_id[case.case_id],
            catalog["caseDigests"][case.case_id],
        )
        for case in sorted(cases, key=lambda item: item.case_id)
    ]
    passed = sum(result.passed for result in results)
    fully_passed = _fully_passed_skill_count(results)
    evidence = {
        "apiVersion": "graphos.io/v2",
        "kind": "PrebundledSkillValidationEvidence",
        "evidenceVersion": 2,
        "generatedAt": generated_at,
        "release": {
            "id": release_id,
            "specificationDigest": release_specification_digest,
            "promotionEvidenceDigest": promotion_evidence_digest,
            "graphOsDigest": graph_os_digest,
            "engineDigest": engine_digest,
        },
        "runtime": {
            "configurationDigest": runtime_config_digest,
            "profileDigest": runtime_profile_digest,
            "modelRegistryDigest": model_registry_digest,
            "sequential": True,
            "metadataOnlyObservability": True,
        },
        "catalog": {
            "skillCount": _SKILL_COUNT,
            "skillCatalogDigest": prebundled_skill_catalog_digest(SKILLS_ROOT),
            "testCaseCount": _CASE_COUNT,
            "testCatalogDigest": catalog["testCatalogDigest"],
            "caseCatalogDigest": catalog["caseCatalogDigest"],
        },
        "cases": evidence_cases,
        "result": _evidence_result_block(passed, fully_passed),
        "privacy": {
            "containsPrompts": False,
            "containsModelOutput": False,
            "containsEndpoints": False,
            "containsCredentials": False,
            "containsIdentities": False,
            "containsFilesystemLocations": False,
            "containsRawTraceIdentifiers": False,
        },
    }
    _clean, privacy = PersistencePrivacyGuard().sanitize(evidence)
    if privacy.changed:
        raise RuntimeError("evidence_privacy_gate_failed")
    return evidence


def render_evidence(evidence: dict[str, Any]) -> str:
    rendered = json.dumps(evidence, sort_keys=True, indent=2) + "\n"
    _report_payload(rendered)
    return rendered
