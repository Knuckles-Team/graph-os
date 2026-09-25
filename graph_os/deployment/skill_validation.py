"""Run one exact-release GraphOS skill certification lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_utilities.skills.runtime_validation import (
    _CASE_COUNT,
    _digest_bytes,
    _external_command,
    publish_report,
    render_evidence,
    sign_and_verify_evidence,
)

from graph_os.deployment.certification_oidc import EphemeralLoopbackOidcAuthority
from graph_os.deployment.skill_validation_core import (
    _ENGINE_MARKER_ENV,
    _MARKER_ENV,
    _PROFILE_ENV,
    DeploymentError,
    SkillValidationDeployment,
    _file_digest,
    _json_without_duplicates,
    _lifecycle_subject,
    _marked_engine_digest,
    _process_counts,
    _regular_executable,
    _runtime_reference,
    _stop_and_reap,
    _terminate_marked_engines,
    _validate_evidence_destinations,
    _wait_for_terminal_process_gate,
    _wait_until_ready,
    load_deployment,
)


@dataclass
class _LifecyclePrep:
    """Bundled read-only artifacts resolved before the try/except/finally
    lifecycle body (keeps every extracted lifecycle-step function's
    parameter count under the cap)."""

    deployment: SkillValidationDeployment
    deployment_path: Path
    report_path: Path
    validation_evidence_path: Path
    start_argv: list[str]
    validator: Path
    readiness_executable: Path
    runtime_materials: dict[str, Any]


@dataclass
class _LifecycleState:
    """Mutable state threaded through the ``run_deployment`` lifecycle body --
    exactly the local variables the original inline function used, now living
    on one object so extracted step functions can mutate them and have the
    mutation visible to every later step (including the ``finally`` reap and
    the final evidence assembly)."""

    marker: str
    authority: EphemeralLoopbackOidcAuthority
    environment: dict[str, str]
    before_global: int
    before_graph_os: int
    before_engine: int
    terminal_process_counts: tuple[int, int]
    running_global: int = 0
    running_graph_os: int = 0
    running_engine: int = 0
    identity_authority_before: int = 0
    identity_authority_running: int = 0
    identity_authority_after: int = 0
    after_global: int = 0
    after_graph_os: int = 0
    after_engine: int = 0
    reaped: bool = False
    identity_tls_verified: bool = False
    renewable_credentials_proven: bool = False
    identity_token_mint_count: int = 0
    model_transport_proof: dict[str, Any] = field(default_factory=dict)
    engine_executable_digest: str | None = None
    validator_exit_code: int | None = None
    validation_digest: str | None = None
    validation_case_count: int = 0
    error_code: str | None = None
    process: subprocess.Popen[bytes] | None = None


def _prepare_lifecycle_run(
    deployment: SkillValidationDeployment,
    *,
    deployment_path: Path,
    report_path: Path,
    validation_evidence_path: Path,
) -> tuple[_LifecyclePrep, bool]:
    """Resolve and attest every artifact a candidate service needs before it
    can be started. Returns ``(prep, installed_release_attested)``; raises
    (uncaught by the lifecycle try/except -- these checks gate whether a
    service may start at all) on any binding/digest mismatch."""
    from graph_os.deployment.skill_validation_assets import (
        attest_installed_release_binding,
        load_runtime_materials,
        verify_release_bindings,
    )

    # No service can start until every supplied digest has been independently
    # recomputed and the exact promotion subject has been externally verified.
    promotion_evidence = verify_release_bindings(deployment)
    runtime_materials = load_runtime_materials(
        deployment, require_active_configuration=True
    )
    start_argv = _external_command(deployment.release.start_command_reference)
    start_executable = _regular_executable(Path(start_argv[0]), name="graph-os")
    start_argv = [str(start_executable), *start_argv[1:]]
    if _file_digest(start_executable) != deployment.release.graph_os_digest:
        raise DeploymentError("graph_os_digest_mismatch")
    attest_installed_release_binding(
        deployment,
        start_executable=start_executable,
        promotion_evidence=promotion_evidence,
    )
    validator = _regular_executable(
        start_executable.with_name("agent-utilities-validate-skills"),
        name="agent-utilities-validate-skills",
    )
    readiness_executable = _regular_executable(
        start_executable.with_name("graph-os-skill-readiness"),
        name="graph-os-skill-readiness",
    )
    _runtime_reference(deployment.runtime.endpoint_reference)
    # Resolve both external signature commands before a service can be started.
    _external_command(deployment.validation.signer_command_reference)
    _external_command(deployment.validation.verifier_command_reference)

    prep = _LifecyclePrep(
        deployment=deployment,
        deployment_path=deployment_path,
        report_path=report_path,
        validation_evidence_path=validation_evidence_path,
        start_argv=start_argv,
        validator=validator,
        readiness_executable=readiness_executable,
        runtime_materials=runtime_materials,
    )
    return prep, True


def _init_lifecycle_state(prep: _LifecyclePrep) -> _LifecycleState:
    deployment = prep.deployment
    marker = secrets.token_hex(32)
    before = _process_counts(marker)
    before_global = before.global_graph_os
    before_graph_os = before.candidate_graph_os
    before_engine = before.candidate_engine
    terminal_process_counts = (
        before.langfuse_mcp_children,
        before.loopback_oidc_fixtures,
    )
    model_transport_proof: dict[str, Any] = {
        "modelCount": 2,
        "literalPrivateModelCount": (
            deployment.runtime.model_registry.literal_private_model_count
        ),
        "privateDnsModelCount": deployment.runtime.model_registry.private_dns_model_count,
        "privateDnsUniqueResolutionProven": False,
        "privateBoundaryProven": False,
        "dnsRebindingGuarded": False,
    }
    authority = EphemeralLoopbackOidcAuthority(
        token_ttl_seconds=deployment.identity_authority.token_ttl_seconds
    )
    environment = dict(os.environ)
    environment[_MARKER_ENV] = marker
    environment[_ENGINE_MARKER_ENV] = marker
    environment[_PROFILE_ENV] = (
        "profile:" + deployment.runtime.profile_digest.removeprefix("sha256:")
    )
    return _LifecycleState(
        marker=marker,
        authority=authority,
        environment=environment,
        before_global=before_global,
        before_graph_os=before_graph_os,
        before_engine=before_engine,
        terminal_process_counts=terminal_process_counts,
        after_global=before_global,
        after_graph_os=before_graph_os,
        after_engine=before_engine,
        model_transport_proof=model_transport_proof,
    )


def _lifecycle_preflight_gate(state: _LifecycleState) -> None:
    if (state.before_global, state.before_graph_os, state.before_engine) != (
        0,
        0,
        0,
    ) or state.terminal_process_counts != (0, 0):
        raise DeploymentError("process_gate_preexisting")


def _lifecycle_prove_model_registry(
    state: _LifecycleState, prep: _LifecyclePrep
) -> list[str]:
    from graph_os.deployment.skill_validation_assets import (
        prove_model_registry_runtime,
    )

    model_private_hosts = prep.runtime_materials.get("modelPrivateHosts")
    models = prep.runtime_materials.get("models")
    if (
        not isinstance(model_private_hosts, list)
        or any(not isinstance(host, str) for host in model_private_hosts)
        or not isinstance(models, list)
    ):
        raise DeploymentError("runtime_model_registry_invalid")
    state.model_transport_proof = prove_model_registry_runtime(
        models, model_private_hosts
    )
    return model_private_hosts


def _lifecycle_start_authority(state: _LifecycleState) -> None:
    state.authority.start()
    state.identity_authority_running = 1 if state.authority.running else 0
    state.identity_tls_verified = state.authority.tls_verified
    if state.identity_authority_running != 1 or not state.identity_tls_verified:
        raise DeploymentError("identity_authority_not_ready")


def _lifecycle_start_candidate(
    prep: _LifecyclePrep, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        prep.start_argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )


def _lifecycle_verify_running(state: _LifecycleState) -> None:
    running = _process_counts(state.marker)
    state.running_global = running.global_graph_os
    state.running_graph_os = running.candidate_graph_os
    state.running_engine = running.candidate_engine
    assert state.process is not None
    if (state.running_global, state.running_graph_os, state.running_engine) != (
        1,
        1,
        1,
    ) or state.process.poll() is not None:
        raise DeploymentError("candidate_process_count_invalid")


def _lifecycle_verify_engine_digest(
    state: _LifecycleState, deployment: SkillValidationDeployment
) -> None:
    state.engine_executable_digest = _marked_engine_digest(state.marker)
    if state.engine_executable_digest != deployment.release.engine_digest:
        raise DeploymentError("candidate_engine_digest_mismatch")


def _lifecycle_run_validator(
    prep: _LifecyclePrep, environment: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    deployment = prep.deployment
    return subprocess.run(
        [
            str(prep.validator),
            "--mode",
            "all",
            "--case-timeout",
            str(deployment.validation.case_timeout_seconds),
            "--report",
            str(prep.report_path),
            "--evidence",
            str(prep.validation_evidence_path),
            "--release-id",
            deployment.release.id,
            "--release-specification-digest",
            deployment.release.specification_digest,
            "--promotion-evidence-digest",
            deployment.release.promotion_evidence_digest,
            "--graph-os-digest",
            deployment.release.graph_os_digest,
            "--engine-digest",
            deployment.release.engine_digest,
            "--runtime-config-digest",
            deployment.runtime.configuration_digest,
            "--runtime-profile-digest",
            deployment.runtime.profile_digest,
            "--model-registry-digest",
            deployment.runtime.model_registry.digest,
            "--signer-command-ref",
            deployment.validation.signer_command_reference,
            "--verifier-command-ref",
            deployment.validation.verifier_command_reference,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=min(
            4 * 60 * 60,
            deployment.validation.case_timeout_seconds * 20 + 20 * 60 + 300,
        ),
        close_fds=True,
        env=environment,
    )


def _lifecycle_verify_validation_evidence(
    state: _LifecycleState, prep: _LifecyclePrep
) -> None:
    validation_payload = prep.validation_evidence_path.read_bytes()
    if not 1 <= len(validation_payload) <= 8 * 1024 * 1024:
        raise DeploymentError("validation_evidence_size_invalid")
    state.validation_digest = _digest_bytes(validation_payload)
    try:
        validation_document = _json_without_duplicates(
            validation_payload.decode("utf-8")
        )
        validation_cases = validation_document.get("cases")
        validation_result = validation_document.get("result")
    except Exception as exc:
        raise DeploymentError("validation_evidence_invalid") from exc
    if (
        not isinstance(validation_cases, list)
        or len(validation_cases) != _CASE_COUNT
        or not isinstance(validation_result, dict)
        or validation_result.get("status") != "pass"
    ):
        raise DeploymentError("validation_evidence_invalid")
    state.validation_case_count = _CASE_COUNT


def _lifecycle_prove_renewable(state: _LifecycleState) -> None:
    state.renewable_credentials_proven = state.authority.prove_renewable()
    state.identity_token_mint_count = state.authority.token_mint_count
    if not state.renewable_credentials_proven or state.identity_token_mint_count < 2:
        raise DeploymentError("identity_authority_not_renewable")


def _lifecycle_stop_processes(
    state: _LifecycleState, deployment: SkillValidationDeployment
) -> None:
    if state.process is not None:
        try:
            _stop_and_reap(state.process, deployment.shutdown.grace_seconds)
        except Exception:
            state.error_code = "candidate_reap_failed"
    try:
        _terminate_marked_engines(state.marker, deployment.shutdown.grace_seconds)
    except Exception:
        state.error_code = "candidate_engine_reap_failed"
    state.identity_tls_verified = (
        state.identity_tls_verified or state.authority.tls_verified
    )
    state.identity_token_mint_count = max(
        state.identity_token_mint_count, state.authority.token_mint_count
    )
    try:
        state.authority.stop()
    except Exception:
        state.error_code = "identity_authority_reap_failed"
    state.identity_authority_after = 1 if state.authority.running else 0


def _lifecycle_finalize_reap_status(state: _LifecycleState) -> None:
    state.reaped = (
        (state.after_global, state.after_graph_os, state.after_engine) == (0, 0, 0)
        and state.identity_authority_after == 0
        and state.terminal_process_counts == (0, 0)
        and (state.process is None or state.process.poll() is not None)
    )
    if not state.reaped:
        state.error_code = (
            "identity_authority_reap_failed"
            if state.identity_authority_after != 0
            else (
                "terminal_process_count_invalid"
                if state.terminal_process_counts != (0, 0)
                else "candidate_process_leaked"
            )
        )


def _lifecycle_reap(
    state: _LifecycleState, deployment: SkillValidationDeployment
) -> None:
    _lifecycle_stop_processes(state, deployment)
    after = _wait_for_terminal_process_gate(
        state.marker, deployment.shutdown.grace_seconds
    )
    state.after_global = after.global_graph_os
    state.after_graph_os = after.candidate_graph_os
    state.after_engine = after.candidate_engine
    state.terminal_process_counts = (
        after.langfuse_mcp_children,
        after.loopback_oidc_fixtures,
    )
    _lifecycle_finalize_reap_status(state)


def run_deployment(
    deployment: SkillValidationDeployment,
    *,
    deployment_path: Path,
    report_path: Path,
    validation_evidence_path: Path,
    lifecycle_evidence_path: Path,
) -> int:
    """Execute one exact zero/one/zero lifecycle and publish signed evidence."""

    _validate_evidence_destinations(
        (report_path, validation_evidence_path, lifecycle_evidence_path)
    )
    prep, installed_release_attested = _prepare_lifecycle_run(
        deployment,
        deployment_path=deployment_path,
        report_path=report_path,
        validation_evidence_path=validation_evidence_path,
    )
    state = _init_lifecycle_state(prep)

    try:
        _lifecycle_preflight_gate(state)
        model_private_hosts = _lifecycle_prove_model_registry(state, prep)
        _lifecycle_start_authority(state)
        state.environment = state.authority.child_environment(
            state.environment,
            model_private_hosts=model_private_hosts,
        )
        state.process = _lifecycle_start_candidate(prep, state.environment)
        _wait_until_ready(
            state.process,
            readiness_executable=prep.readiness_executable,
            deployment_path=prep.deployment_path,
            environment=state.environment,
            timeout_seconds=prep.deployment.readiness.timeout_seconds,
            poll_interval_milliseconds=prep.deployment.readiness.poll_interval_milliseconds,
        )
        _lifecycle_verify_running(state)
        _lifecycle_verify_engine_digest(state, prep.deployment)
        completed = _lifecycle_run_validator(prep, state.environment)
        state.validator_exit_code = completed.returncode
        if completed.returncode != 0:
            raise DeploymentError("skill_validator_failed")
        _lifecycle_verify_validation_evidence(state, prep)
        _lifecycle_prove_renewable(state)
    except DeploymentError as exc:
        state.error_code = str(exc)
    except Exception:
        state.error_code = "deployment_boundary_failed"
    finally:
        _lifecycle_reap(state, prep.deployment)

    unsigned = _lifecycle_subject(
        deployment,
        global_counts=(state.before_global, state.running_global, state.after_global),
        graph_os_counts=(
            state.before_graph_os,
            state.running_graph_os,
            state.after_graph_os,
        ),
        engine_counts=(
            state.before_engine,
            state.running_engine,
            state.after_engine,
        ),
        identity_authority_counts=(
            state.identity_authority_before,
            state.identity_authority_running,
            state.identity_authority_after,
        ),
        terminal_process_counts=state.terminal_process_counts,
        identity_tls_verified=state.identity_tls_verified,
        renewable_credentials_proven=state.renewable_credentials_proven,
        identity_token_mint_count=state.identity_token_mint_count,
        model_transport_proof=state.model_transport_proof,
        engine_executable_digest=state.engine_executable_digest,
        installed_release_attested=installed_release_attested,
        reaped=state.reaped,
        validator_exit_code=state.validator_exit_code,
        validation_evidence_digest=state.validation_digest,
        validation_case_count=state.validation_case_count,
        error_code=state.error_code,
    )
    signed = sign_and_verify_evidence(
        unsigned,
        signer_reference=deployment.validation.signer_command_reference,
        verifier_reference=deployment.validation.verifier_command_reference,
    )
    publish_report(lifecycle_evidence_path, render_evidence(signed))
    return 0 if signed["result"] == "pass" else 1


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="graph-os-certify-skills")
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--validation-evidence", type=Path, required=True)
    parser.add_argument("--lifecycle-evidence", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        deployment = load_deployment(args.deployment)
        return run_deployment(
            deployment,
            deployment_path=args.deployment,
            report_path=args.report,
            validation_evidence_path=args.validation_evidence,
            lifecycle_evidence_path=args.lifecycle_evidence,
        )
    except Exception as exc:  # noqa: BLE001 - never expose runtime material
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
