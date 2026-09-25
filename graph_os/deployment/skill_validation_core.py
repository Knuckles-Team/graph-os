"""Deployment-owned exact-release orchestration for bundled-skill certification.

The command starts one exact candidate GraphOS process from an externally supplied
JSON argv reference, proves readiness through the packaged AgentConfig/TLS/OIDC
probe, invokes the validator from the same installed release, and always stops and
reaps the marked GraphOS and local engine. Durable configuration contains only
digests, booleans, bounds, counts, and environment-reference names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_utilities.core._env import setting
from agent_utilities.skills.runtime_validation import (
    _CASE_COUNT,
    load_matrix,
    minimum_campaign_authority_ttl_seconds,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DIGEST = re.compile(r"^sha256:(?!0{64}$)[a-f0-9]{64}$")
_MARKER_ENV = "GRAPHOS_SKILL_VALIDATION_INSTANCE"
# The engine launcher deliberately inherits only documented engine namespaces;
# this marker uses that trusted prefix so the exact local Rust child can prove
# descent without widening its sanitized environment.
_ENGINE_MARKER_ENV = "EPISTEMIC_GRAPH_SKILL_VALIDATION_INSTANCE"
_PROFILE_ENV = "AGENT_UTILITIES_RUNTIME_PROFILE_REF"


@dataclass(frozen=True)
class _ProcessCounts:
    """Privacy-safe process classes plus internal marked-engine handles."""

    global_graph_os: int
    candidate_graph_os: int
    candidate_engine: int
    langfuse_mcp_children: int
    loopback_oidc_fixtures: int
    marked_engines: tuple[Path, ...]


class DeploymentError(RuntimeError):
    """Controlled lifecycle failure whose message is never reported."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class ReleaseBinding(_StrictModel):
    id: str = Field(pattern=r"^release-[a-z0-9][a-z0-9.-]{2,63}$")
    specification_reference: str = Field(
        alias="specificationReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    specification_digest: str = Field(alias="specificationDigest")
    promotion_evidence_reference: str = Field(
        alias="promotionEvidenceReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    promotion_evidence_digest: str = Field(alias="promotionEvidenceDigest")
    agent_utilities_sha256: str = Field(alias="agentUtilitiesSha256")
    agent_utilities_file_count: int = Field(alias="agentUtilitiesFileCount", ge=10)
    distribution_closure_sha256: str = Field(alias="distributionClosureSha256")
    release_python_sha256: str = Field(alias="releasePythonSha256")
    graph_os_digest: str = Field(alias="graphOsDigest")
    engine_digest: str = Field(alias="engineDigest")
    start_command_reference: str = Field(
        alias="startCommandReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )

    @field_validator(
        "specification_digest",
        "promotion_evidence_digest",
        "agent_utilities_sha256",
        "distribution_closure_sha256",
        "release_python_sha256",
        "graph_os_digest",
        "engine_digest",
    )
    @classmethod
    def _digest(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("digest_invalid")
        return value


class ModelRegistryBinding(_StrictModel):
    digest: str
    model_count: Literal[2] = Field(alias="modelCount")
    light_count: Literal[1] = Field(alias="lightCount")
    normal_count: Literal[1] = Field(alias="normalCount")
    local_private_transport_only: Literal[True] = Field(
        alias="localPrivateTransportOnly"
    )
    reference_backed_credentials_only: Literal[True] = Field(
        alias="referenceBackedCredentialsOnly"
    )
    literal_private_model_count: int = Field(
        alias="literalPrivateModelCount", ge=0, le=2
    )
    private_dns_model_count: int = Field(alias="privateDnsModelCount", ge=0, le=2)
    runtime_private_resolution_required: Literal[True] = Field(
        alias="runtimePrivateResolutionRequired"
    )

    @field_validator("digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("digest_invalid")
        return value

    @model_validator(mode="after")
    def _model_transport_cardinality(self) -> ModelRegistryBinding:
        if self.literal_private_model_count + self.private_dns_model_count != 2:
            raise ValueError("model_transport_cardinality_invalid")
        return self


class RuntimeBinding(_StrictModel):
    configuration_reference: str = Field(
        alias="configurationReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    configuration_digest: str = Field(alias="configurationDigest")
    profile_reference: str = Field(
        alias="profileReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    profile_digest: str = Field(alias="profileDigest")
    endpoint_reference: str = Field(
        alias="endpointReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    model_registry: ModelRegistryBinding = Field(alias="modelRegistry")

    @field_validator("profile_digest", "configuration_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("digest_invalid")
        return value


class ReadinessBinding(_StrictModel):
    timeout_seconds: int = Field(alias="timeoutSeconds", ge=1, le=300)
    poll_interval_milliseconds: int = Field(
        alias="pollIntervalMilliseconds", ge=50, le=5_000
    )


class ValidationBinding(_StrictModel):
    case_timeout_seconds: int = Field(alias="caseTimeoutSeconds", ge=1, le=600)
    signer_command_reference: str = Field(
        alias="signerCommandReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )
    verifier_command_reference: str = Field(
        alias="verifierCommandReference", pattern=r"^[A-Z][A-Z0-9_]{2,63}$"
    )


class ShutdownBinding(_StrictModel):
    grace_seconds: int = Field(alias="graceSeconds", ge=1, le=60)


class IdentityAuthorityBinding(_StrictModel):
    mode: Literal["ephemeral-https-loopback"]
    token_ttl_seconds: int = Field(alias="tokenTtlSeconds", ge=180, le=3_600)
    tls_verification_required: Literal[True] = Field(alias="tlsVerificationRequired")
    lifecycle_owned: Literal[True] = Field(alias="lifecycleOwned")
    renewable_credentials_required: Literal[True] = Field(
        alias="renewableCredentialsRequired"
    )


class SkillValidationDeployment(_StrictModel):
    api_version: Literal["graphos.io/v2"] = Field(alias="apiVersion")
    kind: Literal["SkillValidationDeployment"]
    identity_authority: IdentityAuthorityBinding = Field(alias="identityAuthority")
    release: ReleaseBinding
    runtime: RuntimeBinding
    readiness: ReadinessBinding
    validation: ValidationBinding
    shutdown: ShutdownBinding

    @model_validator(mode="after")
    def _identity_lease_covers_campaign(self) -> SkillValidationDeployment:
        defaults, _cases = load_matrix()
        trace_timeout = defaults.get("trace_timeout_seconds")
        if (
            isinstance(trace_timeout, bool)
            or not isinstance(trace_timeout, int | float)
            or trace_timeout <= 0
        ):
            raise ValueError("campaign_trace_timeout_invalid")
        required_ttl = minimum_campaign_authority_ttl_seconds(
            case_timeout=self.validation.case_timeout_seconds,
            trace_timeout=float(trace_timeout),
            shutdown_grace=self.shutdown.grace_seconds,
        )
        if self.identity_authority.token_ttl_seconds < required_ttl:
            raise ValueError("identity_token_ttl_campaign_window_invalid")
        return self


def _json_without_duplicates(payload: str) -> Any:
    def exact_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DeploymentError("configuration_duplicate_key")
            value[key] = item
        return value

    return json.loads(payload, object_pairs_hook=exact_pairs)


def _read_deployment_bytes(path: Path) -> bytes:
    """Read the deployment config via an ``O_NOFOLLOW`` fd, verifying the
    file is unchanged (dev/inode/size/mtime/ctime) across the read and
    matches what a fresh ``lstat`` of the path sees -- a TOCTOU guard."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= 64 * 1024
        ):
            raise DeploymentError("configuration_not_regular")
        before = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        raw = bytearray()
        while len(raw) <= 64 * 1024:
            chunk = os.read(descriptor, min(64 * 1024 + 1 - len(raw), 65_536))
            if not chunk:
                break
            raw.extend(chunk)
        after_metadata = os.fstat(descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
            after_metadata.st_ctime_ns,
        )
        if before != after or len(raw) != metadata.st_size:
            raise DeploymentError("configuration_changed_during_read")
        path_metadata = path.stat(follow_symlinks=False)
        if (
            path_metadata.st_dev,
            path_metadata.st_ino,
            path_metadata.st_size,
            path_metadata.st_mtime_ns,
            path_metadata.st_ctime_ns,
        ) != before:
            raise DeploymentError("configuration_changed_during_read")
    finally:
        os.close(descriptor)
    return bytes(raw)


def load_deployment(path: Path) -> SkillValidationDeployment:
    try:
        payload = _read_deployment_bytes(path).decode("utf-8")
        return SkillValidationDeployment.model_validate(
            _json_without_duplicates(payload)
        )
    except DeploymentError:
        raise
    except Exception as exc:
        raise DeploymentError("configuration_invalid") from exc


def _runtime_reference(name: str) -> str:
    value = str(setting(name, "") or "")
    if not value or "\x00" in value or len(value) > 4_096:
        raise DeploymentError("runtime_reference_unresolved")
    return value


def _regular_executable(path: Path, *, name: str) -> Path:
    if not path.is_absolute() or path.name != name:
        raise DeploymentError("release_executable_invalid")
    try:
        original = path.lstat()
        canonical = path.resolve(strict=True)
        metadata = canonical.lstat()
    except OSError as exc:
        raise DeploymentError("release_executable_invalid") from exc
    # Every check below is an independent invalidity signal (symlink swap,
    # wrong name, not a regular file, dev/inode changed underneath us, not
    # executable) -- a flat "is this the exact expected release binary"
    # guard, not branching control flow.
    invalid = any(
        (
            stat.S_ISLNK(original.st_mode),
            not stat.S_ISREG(original.st_mode),
            canonical.name != name,
            canonical.is_symlink(),
            not stat.S_ISREG(metadata.st_mode),
            (original.st_dev, original.st_ino) != (metadata.st_dev, metadata.st_ino),
            not os.access(canonical, os.X_OK),
        )
    )
    if invalid:
        raise DeploymentError("release_executable_invalid")
    return canonical


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= 2 * 1024 * 1024 * 1024
        ):
            raise DeploymentError("release_executable_size_invalid")
        consumed = 0
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            consumed += len(chunk)
            if consumed > 2 * 1024 * 1024 * 1024:
                raise DeploymentError("release_executable_size_invalid")
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    try:
        path_metadata = path.stat()
    except OSError as exc:
        raise DeploymentError("release_executable_changed_during_read") from exc
    if (
        consumed != metadata.st_size
        or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or (
            path_metadata.st_dev,
            path_metadata.st_ino,
            path_metadata.st_size,
            path_metadata.st_mtime_ns,
            path_metadata.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise DeploymentError("release_executable_changed_during_read")
    return "sha256:" + digest.hexdigest()


def _validate_evidence_destinations(destinations: tuple[Path, Path, Path]) -> None:
    """Require three fresh sibling outputs before any release process can start."""

    normalized = tuple(Path(os.path.abspath(path)) for path in destinations)
    if len(set(normalized)) != len(normalized):
        raise DeploymentError("evidence_destinations_not_distinct")
    if len({path.parent for path in normalized}) != 1:
        raise DeploymentError("evidence_destinations_not_alongside")
    for path in normalized:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DeploymentError("evidence_destination_unavailable") from exc
        raise DeploymentError("evidence_destination_not_fresh")


def _bounded_proc_bytes(path: Path, *, limit: int) -> bytes:
    try:
        with path.open("rb") as handle:
            payload = handle.read(limit + 1)
    except (FileNotFoundError, ProcessLookupError):
        return b""
    except OSError as exc:
        raise DeploymentError("process_observation_unavailable") from exc
    if len(payload) > limit:
        raise DeploymentError("process_observation_boundary_exceeded")
    return payload


# Checked in this priority order against the candidate executable/argv[:3]
# basenames -- first match wins (dict insertion order is preserved).
_CANDIDATE_KIND_BY_NAME = {
    "epistemic-graph-server": "engine",
    "graph-os": "graph-os",
    "langfuse-mcp": "langfuse-mcp-child",
    "loopback_oidc.py": "loopback-oidc-fixture",
}

# Checked against the module name following a bare `-m` argv token.
_MODULE_KIND_BY_NAME = {
    "agent_utilities.mcp.kg_server": "graph-os",
    "langfuse_agent.mcp_server": "langfuse-mcp-child",
    "scripts.certification.loopback_oidc": "loopback-oidc-fixture",
}


def _kind_from_candidates(candidates: set[str]) -> str | None:
    for candidate_name, kind in _CANDIDATE_KIND_BY_NAME.items():
        if candidate_name in candidates:
            return kind
    return None


def _kind_from_module_argv(argv: list[str]) -> str | None:
    for index, argument in enumerate(argv[:-1]):
        if argument != "-m":
            continue
        kind = _MODULE_KIND_BY_NAME.get(argv[index + 1])
        if kind is not None:
            return kind
    return None


def _process_kind(entry: Path) -> str | None:
    try:
        executable = Path(os.readlink(entry / "exe")).name
    except OSError:
        executable = ""
    raw = _bounded_proc_bytes(entry / "cmdline", limit=64 * 1024)
    try:
        argv = [item.decode("utf-8", "strict") for item in raw.split(b"\x00") if item]
    except UnicodeDecodeError as exc:
        raise DeploymentError("process_observation_invalid") from exc
    candidates = {executable}
    candidates.update(Path(item).name for item in argv[:3])
    kind = _kind_from_candidates(candidates)
    if kind is not None:
        return kind
    return _kind_from_module_argv(argv)


def _process_snapshot(marker: str) -> list[tuple[Path, str, bool]]:
    """Return a bounded internal snapshot; process identities never leave memory."""

    proc = Path("/proc")
    if os.name != "posix" or not proc.is_dir():
        raise DeploymentError("process_observation_unsupported")
    snapshots: list[tuple[Path, str, bool]] = []
    scanned = 0
    graph_marker = f"{_MARKER_ENV}={marker}".encode()
    engine_marker = f"{_ENGINE_MARKER_ENV}={marker}".encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        scanned += 1
        if scanned > 262_144:
            raise DeploymentError("process_observation_boundary_exceeded")
        kind = _process_kind(entry)
        if kind is None:
            continue
        marker_present = False
        if kind in {"graph-os", "engine"}:
            variables = _bounded_proc_bytes(entry / "environ", limit=1024 * 1024).split(
                b"\x00"
            )
            marker_present = (
                graph_marker in variables
                if kind == "graph-os"
                else engine_marker in variables
            )
        snapshots.append((entry, kind, marker_present))
    return snapshots


def _process_counts(marker: str) -> _ProcessCounts:
    snapshot = _process_snapshot(marker)
    global_graph_os = sum(kind == "graph-os" for _entry, kind, _marked in snapshot)
    candidate_graph_os = sum(
        kind == "graph-os" and marked for _entry, kind, marked in snapshot
    )
    candidate_engines = tuple(
        entry for entry, kind, marked in snapshot if kind == "engine" and marked
    )
    return _ProcessCounts(
        global_graph_os=global_graph_os,
        candidate_graph_os=candidate_graph_os,
        candidate_engine=len(candidate_engines),
        langfuse_mcp_children=sum(
            kind == "langfuse-mcp-child" for _entry, kind, _marked in snapshot
        ),
        loopback_oidc_fixtures=sum(
            kind == "loopback-oidc-fixture" for _entry, kind, _marked in snapshot
        ),
        marked_engines=candidate_engines,
    )


def _wait_for_terminal_process_gate(marker: str, grace_seconds: int) -> _ProcessCounts:
    """Wait for lifecycle-owned process teardown to become observable.

    MCP stdio children run in their own sessions.  GraphOS awaits their shutdown,
    but a child that has already been asked to exit can remain visible in
    ``/proc`` briefly after the GraphOS process itself is reaped.  Publishing the
    first post-shutdown snapshot therefore turns an ordinary process-reaping
    race into a false lifecycle failure.  Poll the complete zero-process gate for
    at most the deployment-owned shutdown grace and return the final observed
    counts; a real leak still fails closed in the caller.
    """

    deadline = time.monotonic() + grace_seconds
    while True:
        counts = _process_counts(marker)
        if (
            counts.global_graph_os,
            counts.candidate_graph_os,
            counts.candidate_engine,
            counts.langfuse_mcp_children,
            counts.loopback_oidc_fixtures,
        ) == (0, 0, 0, 0, 0):
            return counts
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return counts
        time.sleep(min(0.05, remaining))


def _marked_engine_digest(marker: str) -> str:
    counts = _process_counts(marker)
    if counts.candidate_engine != 1:
        raise DeploymentError("candidate_engine_count_invalid")
    try:
        return _file_digest(counts.marked_engines[0] / "exe")
    except OSError as exc:
        raise DeploymentError("candidate_engine_digest_unavailable") from exc


def _terminate_marked_engines(marker: str, grace_seconds: int) -> None:
    counts = _process_counts(marker)
    for entry in counts.marked_engines:
        try:
            os.kill(int(entry.name), signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if _process_counts(marker).candidate_engine == 0:
            return
        time.sleep(0.05)
    for entry in _process_counts(marker).marked_engines:
        try:
            os.kill(int(entry.name), signal.SIGKILL)
        except ProcessLookupError:
            continue


def _stop_and_reap(process: subprocess.Popen[bytes], grace_seconds: int) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=grace_seconds)
    else:
        process.wait()


def _wait_until_ready(
    process: subprocess.Popen[bytes],
    *,
    readiness_executable: Path,
    deployment_path: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    poll_interval_milliseconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DeploymentError("candidate_exited_before_ready")
        remaining = max(0.1, deadline - time.monotonic())
        try:
            completed = subprocess.run(
                [
                    str(readiness_executable),
                    "--deployment",
                    str(deployment_path),
                    "--request-timeout",
                    str(min(15.0, remaining)),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=min(20.0, remaining + 1.0),
                close_fds=True,
                env=environment,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(min(poll_interval_milliseconds / 1000.0, remaining))
    raise DeploymentError("candidate_readiness_timeout")


def _lifecycle_subject(
    deployment: SkillValidationDeployment,
    *,
    global_counts: tuple[int, int, int],
    graph_os_counts: tuple[int, int, int],
    engine_counts: tuple[int, int, int],
    identity_authority_counts: tuple[int, int, int],
    terminal_process_counts: tuple[int, int],
    identity_tls_verified: bool,
    renewable_credentials_proven: bool,
    identity_token_mint_count: int,
    model_transport_proof: dict[str, Any],
    engine_executable_digest: str | None,
    installed_release_attested: bool,
    reaped: bool,
    validator_exit_code: int | None,
    validation_evidence_digest: str | None,
    validation_case_count: int,
    error_code: str | None,
) -> dict[str, Any]:
    expected_model_transport_proof = {
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
    }
    # Every prerequisite below is an independent, side-effect-free predicate --
    # a flat "did every exact-lifecycle invariant hold" check, not branching
    # control flow, so `all()` over the list expresses it directly instead of
    # a long `and` chain.
    passed = all(
        (
            global_counts == (0, 1, 0),
            graph_os_counts == (0, 1, 0),
            engine_counts == (0, 1, 0),
            identity_authority_counts == (0, 1, 0),
            terminal_process_counts == (0, 0),
            identity_tls_verified,
            renewable_credentials_proven,
            identity_token_mint_count >= 2,
            model_transport_proof == expected_model_transport_proof,
            engine_executable_digest == deployment.release.engine_digest,
            installed_release_attested,
            reaped,
            validator_exit_code == 0,
            validation_evidence_digest is not None,
            validation_case_count == _CASE_COUNT,
            error_code is None,
        )
    )

    def counts(value: tuple[int, int, int]) -> dict[str, int]:
        return {"before": value[0], "running": value[1], "after": value[2]}

    return {
        "apiVersion": "graphos.io/v2",
        "kind": "SkillValidationLifecycleEvidence",
        "evidenceVersion": 2,
        "release": {
            "id": deployment.release.id,
            "specificationDigest": deployment.release.specification_digest,
            "promotionEvidenceDigest": deployment.release.promotion_evidence_digest,
            "agentUtilitiesSha256": deployment.release.agent_utilities_sha256,
            "agentUtilitiesFileCount": deployment.release.agent_utilities_file_count,
            "distributionClosureSha256": deployment.release.distribution_closure_sha256,
            "releasePythonSha256": deployment.release.release_python_sha256,
            "graphOsDigest": deployment.release.graph_os_digest,
            "engineDigest": deployment.release.engine_digest,
        },
        "runtime": {
            "configurationDigest": deployment.runtime.configuration_digest,
            "profileDigest": deployment.runtime.profile_digest,
            "modelRegistryDigest": deployment.runtime.model_registry.digest,
        },
        "identityAuthority": {
            "mode": deployment.identity_authority.mode,
            "lifecycleCounts": counts(identity_authority_counts),
            "tlsVerified": identity_tls_verified,
            "renewableCredentialsProven": renewable_credentials_proven,
            "tokenMintCount": identity_token_mint_count,
            "reaped": identity_authority_counts[2] == 0,
        },
        "modelTransportProof": model_transport_proof,
        "processGate": {
            "globalGraphOs": counts(global_counts),
            "candidateGraphOs": counts(graph_os_counts),
            "candidateEngine": counts(engine_counts),
            "terminalProcessCounts": {
                "langfuseMcpChildren": terminal_process_counts[0],
                "loopbackOidcFixtures": terminal_process_counts[1],
            },
            "engineExecutableDigest": engine_executable_digest,
            "installedReleaseAttested": installed_release_attested,
            "reaped": reaped,
        },
        "validation": {
            "exitCode": validator_exit_code,
            "evidenceDigest": validation_evidence_digest,
            "caseCount": validation_case_count,
        },
        "result": "pass" if passed else "fail",
        "errorCode": error_code,
        "privacy": {
            "containsEndpoints": False,
            "containsCredentials": False,
            "containsProfiles": False,
            "containsFilesystemLocations": False,
            "containsIdentities": False,
            "containsContent": False,
        },
    }
