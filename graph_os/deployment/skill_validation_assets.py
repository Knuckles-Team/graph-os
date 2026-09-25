"""Generate and verify exact-release bundled-skill certification artifacts."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_utilities.core._env import setting
from jsonschema import Draft202012Validator

from graph_os.deployment.skill_catalog import prebundled_skill_catalog_digest
from graph_os.deployment.skill_validation_assets_core import (
    _CASE_COUNT,
    _MAX_EVIDENCE_BYTES,
    _MAX_MATERIAL_BYTES,
    _SKILL_COUNT,
    CertificationAssetError,
    _configuration_proof,
    _digest,
    _hash_regular,
    _identity_authority_configuration,
    _json_without_duplicates,
    _promotion_certification_binding,
    _read_regular,
    _validate_profile,
    attest_installed_release_binding,
    derive_model_registry_proof,
    generate_runtime_profile,
    load_runtime_materials,
    prove_model_registry_runtime,
    verify_release_bindings,
)
from graph_os.deployment.skill_validation_core import SkillValidationDeployment
from graph_os.deployment.skills import BUNDLED_SKILLS

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"


def generate_deployment(
    *,
    release_id: str,
    release_specification: Path,
    promotion_evidence: Path,
    runtime_configuration: Path,
    runtime_profile: Path,
    specification_reference: str,
    promotion_evidence_reference: str,
    configuration_reference: str,
    profile_reference: str,
    endpoint_reference: str,
    start_command_reference: str,
    signer_command_reference: str,
    verifier_command_reference: str,
    readiness_timeout_seconds: int,
    poll_interval_milliseconds: int,
    case_timeout_seconds: int,
    shutdown_grace_seconds: int,
) -> SkillValidationDeployment:
    """Generate one closed deployment from exact external release/runtime inputs."""

    from graph_os.deployment.skill_validation import SkillValidationDeployment
    from graph_os.deployment.skills.runtime_validation import _external_command

    specification = _read_regular(
        release_specification,
        limit=_MAX_MATERIAL_BYTES,
        code="release_specification_invalid",
    )
    evidence_payload = _read_regular(
        promotion_evidence,
        limit=_MAX_EVIDENCE_BYTES,
        code="promotion_evidence_invalid",
    )
    configuration = _read_regular(
        runtime_configuration,
        limit=_MAX_MATERIAL_BYTES,
        code="runtime_configuration_invalid",
    )
    profile = _read_regular(
        runtime_profile, limit=_MAX_MATERIAL_BYTES, code="runtime_profile_invalid"
    )
    configuration_digest = _digest(configuration)
    proof = _configuration_proof(configuration)
    identity_authority = _identity_authority_configuration(
        _json_without_duplicates(configuration, code="runtime_configuration_invalid")
    )
    _validate_profile(
        profile,
        configuration_digest=configuration_digest,
        model_registry_digest=proof["digest"],
        identity_authority=identity_authority,
    )
    try:
        from scripts.release.promote_local_release import verify_evidence_file

        evidence = verify_evidence_file(
            spec_path=release_specification,
            release_id=release_id,
            evidence_path=promotion_evidence,
        )
    except Exception as exc:
        raise CertificationAssetError("promotion_evidence_verification_failed") from exc
    certification = evidence.get("certificationArtifacts")
    if evidence.get("status") != "promoted" or not isinstance(certification, dict):
        raise CertificationAssetError("promotion_evidence_not_promoted")
    release_binding = _promotion_certification_binding(certification)
    graph_os_digest = release_binding["graphOsDigest"]
    engine_digest = release_binding["engineDigest"]
    start_argv = _external_command(start_command_reference)
    try:
        executable = Path(start_argv[0]).resolve(strict=True)
    except OSError as exc:
        raise CertificationAssetError("graph_os_executable_invalid") from exc
    if executable.name != "graph-os":
        raise CertificationAssetError("graph_os_executable_invalid")
    if (
        _hash_regular(
            executable,
            limit=2 * 1024 * 1024 * 1024,
            code="graph_os_executable_invalid",
        )
        != graph_os_digest
    ):
        raise CertificationAssetError("graph_os_digest_mismatch")
    deployment = SkillValidationDeployment.model_validate(
        {
            "apiVersion": "graphos.io/v2",
            "kind": "SkillValidationDeployment",
            "identityAuthority": identity_authority,
            "release": {
                "id": release_id,
                "specificationReference": specification_reference,
                "specificationDigest": _digest(specification),
                "promotionEvidenceReference": promotion_evidence_reference,
                "promotionEvidenceDigest": _digest(evidence_payload),
                "agentUtilitiesSha256": release_binding["agentUtilitiesSha256"],
                "agentUtilitiesFileCount": release_binding["agentUtilitiesFileCount"],
                "distributionClosureSha256": release_binding[
                    "distributionClosureSha256"
                ],
                "releasePythonSha256": release_binding["releasePythonSha256"],
                "graphOsDigest": graph_os_digest,
                "engineDigest": engine_digest,
                "startCommandReference": start_command_reference,
            },
            "runtime": {
                "configurationReference": configuration_reference,
                "configurationDigest": configuration_digest,
                "profileReference": profile_reference,
                "profileDigest": _digest(profile),
                "endpointReference": endpoint_reference,
                "modelRegistry": proof,
            },
            "readiness": {
                "timeoutSeconds": readiness_timeout_seconds,
                "pollIntervalMilliseconds": poll_interval_milliseconds,
            },
            "validation": {
                "caseTimeoutSeconds": case_timeout_seconds,
                "signerCommandReference": signer_command_reference,
                "verifierCommandReference": verifier_command_reference,
            },
            "shutdown": {"graceSeconds": shutdown_grace_seconds},
        }
    )
    attest_installed_release_binding(
        deployment,
        start_executable=executable,
        promotion_evidence=evidence,
    )
    return deployment


def _validated_request_timeout(request_timeout: float) -> None:
    if (
        isinstance(request_timeout, bool)
        or not isinstance(request_timeout, int | float)
        or not math.isfinite(float(request_timeout))
        or not 0.1 <= float(request_timeout) <= 120.0
    ):
        raise CertificationAssetError("readiness_timeout_invalid")


def _validated_runtime_transport_proof(
    deployment: SkillValidationDeployment, runtime_materials: dict[str, Any]
) -> None:
    runtime_transport_proof = prove_model_registry_runtime(
        runtime_materials["models"], runtime_materials["modelPrivateHosts"]
    )
    if (
        runtime_transport_proof["literalPrivateModelCount"]
        != deployment.runtime.model_registry.literal_private_model_count
        or runtime_transport_proof["privateDnsModelCount"]
        != deployment.runtime.model_registry.private_dns_model_count
    ):
        raise CertificationAssetError("runtime_model_transport_proof_mismatch")


def _resolved_local_endpoint(endpoint: str) -> tuple[Any, str, int | None, bool]:
    try:
        parsed = urlsplit(endpoint)
        host = str(parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise CertificationAssetError("graph_os_endpoint_not_local") from exc
    try:
        address = ipaddress.ip_address(host)
        loopback = address.is_loopback
    except ValueError:
        loopback = host in {"localhost", "localhost.localdomain"}
    return parsed, host, port, loopback


def _validate_local_endpoint(parsed: Any, port: int | None, loopback: bool) -> None:
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not loopback
        or (port is not None and not 1 <= port <= 65_535)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CertificationAssetError("graph_os_endpoint_not_local")


def _validate_active_runtime_configuration(
    deployment: SkillValidationDeployment, endpoint: str, config: Any
) -> None:
    if str(config.mcp_url or "").strip() != endpoint:
        raise CertificationAssetError("graph_os_endpoint_not_active")
    active_proof = derive_model_registry_proof(
        list(config.chat_models), list(config.model_http_allowed_private_hosts)
    )
    if active_proof != deployment.runtime.model_registry.model_dump(by_alias=True):
        raise CertificationAssetError("runtime_model_registry_not_active")


async def _probe_graph_os_tools(endpoint: str, *, request_timeout: float) -> None:
    from agent_utilities.mcp.client_credentials import child_auth
    from agent_utilities.mcp.toolset_factory import build_http_toolset

    from graph_os.deployment.skills.runtime_validation import _call_tool, _ensure_tool

    toolset = build_http_toolset(
        endpoint,
        auth=child_auth({}),
        timeout=request_timeout,
        toolset_id="skill-certification-readiness",
    )
    async with toolset.client as client:
        for tool in ("graph_orchestrate", "graph_query", "graph_jobs"):
            await _ensure_tool(client, tool, request_timeout)
        await _call_tool(
            client,
            "graph_query",
            {
                "query": "MATCH (n) RETURN n LIMIT 0",
                "params": "{}",
                "scope": "local",
            },
            request_timeout,
        )


async def probe_readiness(
    deployment: SkillValidationDeployment, *, request_timeout: float = 15.0
) -> None:
    """Prove active AgentConfig, TLS/auth, GraphOS tools, and a local engine."""

    _validated_request_timeout(request_timeout)
    runtime_materials = load_runtime_materials(
        deployment, require_active_configuration=True
    )
    _validated_runtime_transport_proof(deployment, runtime_materials)
    endpoint = str(setting(deployment.runtime.endpoint_reference, "") or "").strip()
    parsed, _host, port, loopback = _resolved_local_endpoint(endpoint)
    _validate_local_endpoint(parsed, port, loopback)

    from agent_utilities.core.config import config
    from agent_utilities.core.transport_security import resolve_configured_tls_profile
    from agent_utilities.knowledge_graph.core.engine_resolver import resolve_engine
    from agent_utilities.knowledge_graph.core.shard_topology import is_local_endpoint
    from agent_utilities.mcp.client_credentials import (
        outbound_auth_configuration_status,
    )

    _validate_active_runtime_configuration(deployment, endpoint, config)
    auth_status = outbound_auth_configuration_status()
    if auth_status.get("ready") is not True:
        raise CertificationAssetError("graph_os_identity_not_ready")
    trust = resolve_configured_tls_profile("mcp", config=config)
    trust.cleanup()
    await _probe_graph_os_tools(endpoint, request_timeout=request_timeout)
    resolved = resolve_engine(config, "skill-validation-readiness")
    if resolved.mode == "remote" or not is_local_endpoint(resolved.endpoint):
        raise CertificationAssetError("engine_topology_not_local")


def _schema(name: str) -> dict[str, Any]:
    payload = files("deploy.release").joinpath(name).read_bytes()
    value = _json_without_duplicates(payload, code="certification_schema_invalid")
    if not isinstance(value, dict):
        raise CertificationAssetError("certification_schema_invalid")
    Draft202012Validator.check_schema(value)
    return value


def _signed_document(path: Path, *, code: str) -> tuple[dict[str, Any], bytes]:
    payload = _read_regular(path, limit=_MAX_EVIDENCE_BYTES, code=code)
    value = _json_without_duplicates(payload, code=code)
    if not isinstance(value, dict):
        raise CertificationAssetError(code)
    return value, payload


def _validate_certification_schemas(
    validation: dict[str, Any], lifecycle: dict[str, Any]
) -> None:
    try:
        Draft202012Validator(
            _schema("prebundled-skill-validation-evidence.schema.json")
        ).validate(validation)
        Draft202012Validator(
            _schema("skill-validation-deployment-evidence.schema.json")
        ).validate(lifecycle)
    except Exception as exc:
        raise CertificationAssetError("certification_evidence_schema_invalid") from exc


def _expected_release_binding(deployment: SkillValidationDeployment) -> dict[str, Any]:
    return {
        "id": deployment.release.id,
        "specificationDigest": deployment.release.specification_digest,
        "promotionEvidenceDigest": deployment.release.promotion_evidence_digest,
        "agentUtilitiesSha256": deployment.release.agent_utilities_sha256,
        "agentUtilitiesFileCount": deployment.release.agent_utilities_file_count,
        "distributionClosureSha256": deployment.release.distribution_closure_sha256,
        "releasePythonSha256": deployment.release.release_python_sha256,
        "graphOsDigest": deployment.release.graph_os_digest,
        "engineDigest": deployment.release.engine_digest,
    }


def _expected_runtime_binding(deployment: SkillValidationDeployment) -> dict[str, Any]:
    return {
        "configurationDigest": deployment.runtime.configuration_digest,
        "profileDigest": deployment.runtime.profile_digest,
        "modelRegistryDigest": deployment.runtime.model_registry.digest,
    }


def _verify_validation_release_binding(
    validation: dict[str, Any], expected_release: dict[str, Any]
) -> None:
    validation_expected_release = {
        key: value
        for key, value in expected_release.items()
        if key
        not in {
            "agentUtilitiesSha256",
            "agentUtilitiesFileCount",
            "distributionClosureSha256",
            "releasePythonSha256",
        }
    }
    if validation.get("release") != validation_expected_release:
        raise CertificationAssetError("validation_release_binding_mismatch")


def _verify_validation_runtime_binding(
    validation: dict[str, Any], expected_runtime: dict[str, Any]
) -> None:
    validation_runtime = validation.get("runtime")
    if not isinstance(validation_runtime, dict) or any(
        validation_runtime.get(key) != value for key, value in expected_runtime.items()
    ):
        raise CertificationAssetError("validation_runtime_binding_mismatch")


def _verify_validation_catalog_binding(
    validation: dict[str, Any], catalogs: dict[str, Any]
) -> None:
    expected_catalog = {
        "skillCount": _SKILL_COUNT,
        "skillCatalogDigest": prebundled_skill_catalog_digest(
            SKILLS_ROOT, skill_names=BUNDLED_SKILLS
        ),
        "testCaseCount": _CASE_COUNT,
        "testCatalogDigest": catalogs["testCatalogDigest"],
        "caseCatalogDigest": catalogs["caseCatalogDigest"],
    }
    if validation.get("catalog") != expected_catalog:
        raise CertificationAssetError("validation_catalog_binding_mismatch")


def _verify_one_case_binding(
    item: Any, expected_cases: dict[str, Any], catalogs: dict[str, Any]
) -> None:
    if not isinstance(item, dict):
        raise CertificationAssetError("validation_case_binding_mismatch")
    case_id = str(item.get("caseId") or "")
    expected_case = expected_cases.get(case_id)
    if expected_case is None or (
        item.get("caseDigest") != catalogs["caseDigests"].get(case_id)
        or item.get("skill") != expected_case.skill
        or item.get("mode") != expected_case.mode
        or item.get("modelClass") != expected_case.model_class
        or item.get("status") != "pass"
    ):
        raise CertificationAssetError("validation_case_binding_mismatch")


def _verify_validation_case_binding(
    validation: dict[str, Any], cases: list[Any], catalogs: dict[str, Any]
) -> None:
    evidence_cases = validation.get("cases")
    expected_cases = {case.case_id: case for case in cases}
    if not isinstance(evidence_cases, list) or len(evidence_cases) != _CASE_COUNT:
        raise CertificationAssetError("validation_case_binding_mismatch")
    for item in evidence_cases:
        _verify_one_case_binding(item, expected_cases, catalogs)


def _verify_validation_result(validation: dict[str, Any]) -> None:
    result = validation.get("result")
    if not isinstance(result, dict) or result.get("status") != "pass":
        raise CertificationAssetError("validation_result_failed")


def _verify_validation_document(
    validation: dict[str, Any],
    expected_release: dict[str, Any],
    expected_runtime: dict[str, Any],
    cases: list[Any],
    catalogs: dict[str, Any],
) -> None:
    _verify_validation_release_binding(validation, expected_release)
    _verify_validation_runtime_binding(validation, expected_runtime)
    _verify_validation_catalog_binding(validation, catalogs)
    _verify_validation_case_binding(validation, cases, catalogs)
    _verify_validation_result(validation)


def _verify_lifecycle_release_runtime_binding(
    lifecycle: dict[str, Any],
    expected_release: dict[str, Any],
    expected_runtime: dict[str, Any],
) -> None:
    if (
        lifecycle.get("release") != expected_release
        or lifecycle.get("runtime") != expected_runtime
    ):
        raise CertificationAssetError("lifecycle_binding_mismatch")


def _verify_lifecycle_identity_authority(
    lifecycle: dict[str, Any], deployment: SkillValidationDeployment
) -> None:
    identity_authority = lifecycle.get("identityAuthority")
    if (
        not isinstance(identity_authority, dict)
        or identity_authority.get("mode") != deployment.identity_authority.mode
        or identity_authority.get("lifecycleCounts")
        != {"before": 0, "running": 1, "after": 0}
        or identity_authority.get("tlsVerified") is not True
        or identity_authority.get("renewableCredentialsProven") is not True
        or isinstance(identity_authority.get("tokenMintCount"), bool)
        or not isinstance(identity_authority.get("tokenMintCount"), int)
        or identity_authority["tokenMintCount"] < 2
        or identity_authority.get("reaped") is not True
    ):
        raise CertificationAssetError("lifecycle_identity_authority_mismatch")


def _verify_lifecycle_model_transport_proof(
    lifecycle: dict[str, Any], deployment: SkillValidationDeployment
) -> None:
    if lifecycle.get("modelTransportProof") != {
        "modelCount": 2,
        "literalPrivateModelCount": (
            deployment.runtime.model_registry.literal_private_model_count
        ),
        "privateDnsModelCount": (
            deployment.runtime.model_registry.private_dns_model_count
        ),
        "privateDnsUniqueResolutionProven": True,
        "privateBoundaryProven": True,
        "dnsRebindingGuarded": True,
    }:
        raise CertificationAssetError("lifecycle_model_transport_proof_mismatch")


def _verify_lifecycle_process_gate(
    lifecycle: dict[str, Any], deployment: SkillValidationDeployment
) -> None:
    process_gate = lifecycle.get("processGate")
    if (
        not isinstance(process_gate, dict)
        or process_gate.get("engineExecutableDigest")
        != deployment.release.engine_digest
    ):
        raise CertificationAssetError("lifecycle_engine_binding_mismatch")
    if process_gate.get("terminalProcessCounts") != {
        "langfuseMcpChildren": 0,
        "loopbackOidcFixtures": 0,
    }:
        raise CertificationAssetError("lifecycle_terminal_process_count_mismatch")


def _verify_lifecycle_validation_binding(
    lifecycle: dict[str, Any], validation_payload: bytes
) -> None:
    lifecycle_validation = lifecycle.get("validation")
    if (
        not isinstance(lifecycle_validation, dict)
        or lifecycle_validation.get("evidenceDigest") != _digest(validation_payload)
        or lifecycle_validation.get("caseCount") != _CASE_COUNT
        or lifecycle.get("result") != "pass"
    ):
        raise CertificationAssetError("lifecycle_validation_binding_mismatch")


def _verify_lifecycle_document(
    lifecycle: dict[str, Any],
    deployment: SkillValidationDeployment,
    expected_release: dict[str, Any],
    expected_runtime: dict[str, Any],
    validation_payload: bytes,
) -> None:
    _verify_lifecycle_release_runtime_binding(
        lifecycle, expected_release, expected_runtime
    )
    _verify_lifecycle_identity_authority(lifecycle, deployment)
    _verify_lifecycle_model_transport_proof(lifecycle, deployment)
    _verify_lifecycle_process_gate(lifecycle, deployment)
    _verify_lifecycle_validation_binding(lifecycle, validation_payload)


def verify_certification_documents(
    *,
    deployment_path: Path,
    validation_evidence_path: Path,
    lifecycle_evidence_path: Path,
) -> None:
    """Independently verify release/runtime bindings and both signed subjects."""

    from graph_os.deployment.skill_validation import load_deployment
    from graph_os.deployment.skills.runtime_validation import (
        _external_command,
        _test_catalog_evidence,
        load_matrix,
        verify_signed_evidence,
    )

    deployment = load_deployment(deployment_path)
    promotion_evidence = verify_release_bindings(deployment)
    load_runtime_materials(deployment, require_active_configuration=False)
    start_argv = _external_command(deployment.release.start_command_reference)
    attest_installed_release_binding(
        deployment,
        start_executable=Path(start_argv[0]),
        promotion_evidence=promotion_evidence,
    )
    validation, validation_payload = _signed_document(
        validation_evidence_path, code="validation_evidence_invalid"
    )
    lifecycle, _lifecycle_payload = _signed_document(
        lifecycle_evidence_path, code="lifecycle_evidence_invalid"
    )
    _validate_certification_schemas(validation, lifecycle)
    verify_signed_evidence(
        validation,
        verifier_reference=deployment.validation.verifier_command_reference,
    )
    verify_signed_evidence(
        lifecycle,
        verifier_reference=deployment.validation.verifier_command_reference,
    )
    expected_release = _expected_release_binding(deployment)
    expected_runtime = _expected_runtime_binding(deployment)
    _defaults, cases = load_matrix()
    catalogs = _test_catalog_evidence(cases)
    _verify_validation_document(
        validation, expected_release, expected_runtime, cases, catalogs
    )
    _verify_lifecycle_document(
        lifecycle, deployment, expected_release, expected_runtime, validation_payload
    )


def _generator_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="graph-os-generate-skill-certification")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--release-specification", type=Path, required=True)
    parser.add_argument("--promotion-evidence", type=Path, required=True)
    parser.add_argument("--runtime-configuration", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--specification-reference", required=True)
    parser.add_argument("--promotion-evidence-reference", required=True)
    parser.add_argument("--configuration-reference", required=True)
    parser.add_argument("--profile-reference", required=True)
    parser.add_argument("--endpoint-reference", required=True)
    parser.add_argument("--start-command-reference", required=True)
    parser.add_argument("--signer-command-reference", required=True)
    parser.add_argument("--verifier-command-reference", required=True)
    parser.add_argument("--readiness-timeout-seconds", type=int, default=120)
    parser.add_argument("--poll-interval-milliseconds", type=int, default=250)
    parser.add_argument("--case-timeout-seconds", type=int, default=120)
    parser.add_argument("--shutdown-grace-seconds", type=int, default=30)
    return parser.parse_args(argv)


def _profile_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="graph-os-generate-skill-runtime-profile")
    parser.add_argument("--configuration-reference", required=True)
    parser.add_argument("--profile-reference", required=True)
    return parser.parse_args(argv)


def profile_main(argv: list[str] | None = None) -> int:
    args = _profile_arguments(argv)
    try:
        generate_runtime_profile(
            configuration_reference=args.configuration_reference,
            profile_reference=args.profile_reference,
        )
    except Exception as exc:  # noqa: BLE001 - never expose external material
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


def generator_main(argv: list[str] | None = None) -> int:
    args = _generator_arguments(argv)
    try:
        deployment = generate_deployment(
            release_id=args.release_id,
            release_specification=args.release_specification,
            promotion_evidence=args.promotion_evidence,
            runtime_configuration=args.runtime_configuration,
            runtime_profile=args.runtime_profile,
            specification_reference=args.specification_reference,
            promotion_evidence_reference=args.promotion_evidence_reference,
            configuration_reference=args.configuration_reference,
            profile_reference=args.profile_reference,
            endpoint_reference=args.endpoint_reference,
            start_command_reference=args.start_command_reference,
            signer_command_reference=args.signer_command_reference,
            verifier_command_reference=args.verifier_command_reference,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
            poll_interval_milliseconds=args.poll_interval_milliseconds,
            case_timeout_seconds=args.case_timeout_seconds,
            shutdown_grace_seconds=args.shutdown_grace_seconds,
        )
        from graph_os.deployment.skills.runtime_validation import publish_report

        publish_report(
            args.output,
            json.dumps(deployment.model_dump(by_alias=True), sort_keys=True, indent=2)
            + "\n",
        )
    except Exception as exc:  # noqa: BLE001 - never expose external material
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


def readiness_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-os-skill-readiness")
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        from graph_os.deployment.skill_validation import load_deployment

        deployment = load_deployment(args.deployment)
        asyncio.run(probe_readiness(deployment, request_timeout=args.request_timeout))
    except Exception as exc:  # noqa: BLE001 - never expose external material
        print(json.dumps({"ready": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({"ready": True}, sort_keys=True))
    return 0


def verifier_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-os-verify-skill-certification")
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--validation-evidence", type=Path, required=True)
    parser.add_argument("--lifecycle-evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verify_certification_documents(
            deployment_path=args.deployment,
            validation_evidence_path=args.validation_evidence,
            lifecycle_evidence_path=args.lifecycle_evidence,
        )
    except Exception as exc:  # noqa: BLE001 - never expose external material
        print(
            json.dumps({"verified": False, "error": type(exc).__name__}, sort_keys=True)
        )
        return 1
    print(json.dumps({"verified": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(generator_main())
