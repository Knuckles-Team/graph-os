"""Packaged assets for exact-release bundled-skill certification.

This module owns the current certification asset generator, the AgentConfig-aware
readiness probe, and independent signed-evidence verification.  Durable outputs
contain only release/runtime digests, aggregate counts, booleans, and environment
reference names.  Endpoint values, credentials, identities, content, commands,
profiles, and filesystem locations remain deployment-owned runtime material.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from agent_utilities.core._env import setting

from graph_os.deployment.certification_oidc import (
    AUTHORITY_MODE,
    DEFAULT_TOKEN_TTL_SECONDS,
    validated_token_ttl_seconds,
)
from graph_os.deployment.skills import BUNDLED_SKILLS

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"

_SKILL_COUNT = len(BUNDLED_SKILLS)
_CASE_COUNT = _SKILL_COUNT * 2

if TYPE_CHECKING:
    from graph_os.deployment.skill_validation_core import SkillValidationDeployment

_MAX_MATERIAL_BYTES = 4 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_DIGEST = re.compile(r"^sha256:(?!0{64}$)[a-f0-9]{64}$")
_PRIVATE_MODEL_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class CertificationAssetError(RuntimeError):
    """Path-free, content-free fail-closed certification error."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_regular_readonly(path: Path, *, code: str) -> int:
    if not path.is_absolute():
        raise CertificationAssetError(code)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise CertificationAssetError(code) from exc


def _validated_regular_stat(
    descriptor: int, *, limit: int, code: str
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= limit
    ):
        raise CertificationAssetError(code)
    return metadata


def _confirm_path_signature(
    path: Path, signature: tuple[int, int, int, int, int], *, code: str
) -> None:
    try:
        path_metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CertificationAssetError(code) from exc
    if _stat_signature(path_metadata) != signature:
        raise CertificationAssetError(code)


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_regular(path: Path, *, limit: int, code: str) -> bytes:
    descriptor = _open_regular_readonly(path, code=code)
    try:
        metadata = _validated_regular_stat(descriptor, limit=limit, code=code)
        before = _stat_signature(metadata)
        payload = _read_bounded(descriptor, limit)
        after = os.fstat(descriptor)
        if (
            len(payload) != metadata.st_size
            or len(payload) > limit
            or before != _stat_signature(after)
        ):
            raise CertificationAssetError(code)
        _confirm_path_signature(path, before, code=code)
        return payload
    finally:
        os.close(descriptor)


def _hash_bounded(descriptor: int, limit: int, hasher: Any, *, code: str) -> int:
    consumed = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > limit:
            raise CertificationAssetError(code)
        hasher.update(chunk)
    return consumed


def _hash_regular(path: Path, *, limit: int, code: str) -> str:
    descriptor = _open_regular_readonly(path, code=code)
    hasher = hashlib.sha256()
    try:
        metadata = _validated_regular_stat(descriptor, limit=limit, code=code)
        before = _stat_signature(metadata)
        consumed = _hash_bounded(descriptor, limit, hasher, code=code)
        after = os.fstat(descriptor)
        if consumed != metadata.st_size or before != _stat_signature(after):
            raise CertificationAssetError(code)
    finally:
        os.close(descriptor)
    _confirm_path_signature(path, _stat_signature(after), code=code)
    return "sha256:" + hasher.hexdigest()


def _json_without_duplicates(payload: bytes, *, code: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise CertificationAssetError(code)
            value[key] = item
        return value

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except CertificationAssetError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationAssetError(code) from exc


def _material_path(reference: str, *, code: str) -> Path:
    if _REFERENCE.fullmatch(reference) is None:
        raise CertificationAssetError(code)
    raw = str(setting(reference, "") or "")
    if not raw or "\x00" in raw or len(raw.encode("utf-8")) > 4_096:
        raise CertificationAssetError(code)
    path = Path(raw)
    if not path.is_absolute():
        raise CertificationAssetError(code)
    return path


def _single_config_key(
    root: dict[str, Any], candidates: tuple[str, ...], *, code: str
) -> str:
    keys = [key for key in candidates if key in root]
    if len(keys) != 1:
        raise CertificationAssetError(code)
    return keys[0]


def _optional_config_key(
    root: dict[str, Any], candidates: tuple[str, ...], *, code: str
) -> str | None:
    keys = [key for key in candidates if key in root]
    if len(keys) > 1:
        raise CertificationAssetError(code)
    return keys[0] if keys else None


def _validated_model_list(raw_models: Any, raw_hosts: Any) -> None:
    if (
        not isinstance(raw_models, list)
        or not isinstance(raw_hosts, list)
        or any(not isinstance(host, str) for host in raw_hosts)
    ):
        raise CertificationAssetError("runtime_model_registry_invalid")


def _model_configuration(root: Any) -> tuple[list[Any], list[str]]:
    if not isinstance(root, dict):
        raise CertificationAssetError("runtime_configuration_invalid")
    from agent_utilities.core.config import ChatModelConfig

    model_key = _single_config_key(
        root, ("CHAT_MODELS", "chat_models"), code="runtime_model_registry_missing"
    )
    host_key = _single_config_key(
        root,
        ("MODEL_HTTP_ALLOWED_PRIVATE_HOSTS", "model_http_allowed_private_hosts"),
        code="runtime_model_registry_missing",
    )
    raw_models = root[model_key]
    raw_hosts = root[host_key]
    _validated_model_list(raw_models, raw_hosts)
    try:
        models = [ChatModelConfig.model_validate(item) for item in raw_models]
    except Exception as exc:
        raise CertificationAssetError("runtime_model_registry_invalid") from exc
    return models, [str(host) for host in raw_hosts]


def _validated_ttl(ttl: Any) -> int:
    try:
        return validated_token_ttl_seconds(ttl)
    except Exception as exc:
        raise CertificationAssetError("runtime_identity_authority_invalid") from exc


def _identity_authority_configuration(root: Any) -> dict[str, Any]:
    """Resolve the current-only lifecycle authority controls from AgentConfig."""

    if not isinstance(root, dict):
        raise CertificationAssetError("runtime_configuration_invalid")
    mode_key = _optional_config_key(
        root,
        ("SKILL_CERT_IDENTITY_AUTHORITY_MODE", "skill_cert_identity_authority_mode"),
        code="runtime_identity_authority_invalid",
    )
    ttl_key = _optional_config_key(
        root,
        (
            "SKILL_CERT_IDENTITY_TOKEN_TTL_SECONDS",
            "skill_cert_identity_token_ttl_seconds",
        ),
        code="runtime_identity_authority_invalid",
    )
    mode = root[mode_key] if mode_key else AUTHORITY_MODE
    ttl = root[ttl_key] if ttl_key else DEFAULT_TOKEN_TTL_SECONDS
    if mode != AUTHORITY_MODE:
        raise CertificationAssetError("runtime_identity_authority_invalid")
    token_ttl_seconds = _validated_ttl(ttl)
    return {
        "mode": AUTHORITY_MODE,
        "tokenTtlSeconds": token_ttl_seconds,
        "tlsVerificationRequired": True,
        "lifecycleOwned": True,
        "renewableCredentialsRequired": True,
    }


def _validate_model_identity(
    model: Any, model_ids: set[str], counts: dict[str, int]
) -> tuple[str, str]:
    model_id = str(getattr(model, "id", "") or "")
    level = str(getattr(model, "intelligence_level", "") or "").casefold()
    if not model_id or model_id in model_ids or level not in counts:
        raise CertificationAssetError("runtime_model_registry_class_invalid")
    model_ids.add(model_id)
    counts[level] += 1
    return model_id, level


def _is_valid_model_transport(
    parsed: Any, host: str, port: int | None, allowed: set[str]
) -> bool:
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    if not host or host not in allowed:
        return False
    if port is not None and not 1 <= port <= 65_535:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    return not (parsed.query or parsed.fragment)


def _validate_model_transport(model: Any, allowed: set[str]) -> tuple[Any, str]:
    try:
        parsed = urlsplit(str(getattr(model, "base_url", "") or ""))
        host = str(parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise CertificationAssetError("runtime_model_transport_invalid") from exc
    if not _is_valid_model_transport(parsed, host, port, allowed):
        raise CertificationAssetError("runtime_model_transport_invalid")
    return parsed, host


def _model_locality(host: str) -> tuple[str, bool, bool]:
    """Return ``(locality label, is_literal_private, is_private_dns)``."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "private-dns-runtime-pinned", False, True
    if address.is_loopback:
        return "loopback", True, False
    if any(address in network for network in _PRIVATE_MODEL_NETWORKS):
        return "private", True, False
    raise CertificationAssetError("runtime_model_locality_unproven")


def _model_auth_modes(model: Any) -> list[str]:
    auth_modes: list[str] = []
    if getattr(model, "api_key_ref", None):
        auth_modes.append("api-key-reference")
    if getattr(model, "oauth2", None):
        auth_modes.append("oauth2-secret-reference")
    if not auth_modes:
        raise CertificationAssetError("runtime_model_credentials_unreferenced")
    if getattr(model, "headers_ref", None):
        auth_modes.append("supplemental-header-reference")
    return auth_modes


def derive_model_registry_proof(
    models: list[Any], allowed_private_hosts: list[str]
) -> dict[str, Any]:
    """Derive a privacy-safe proof for the exact light/normal model registry."""

    allowed = {
        str(host).strip().casefold().rstrip(".") for host in allowed_private_hosts
    }
    if len(models) != 2 or len(allowed) > 256:
        raise CertificationAssetError("runtime_model_registry_cardinality_invalid")
    canonical: list[dict[str, str]] = []
    counts = {"light": 0, "normal": 0}
    literal_private_model_count = 0
    private_dns_model_count = 0
    model_ids: set[str] = set()
    for model in models:
        model_id, level = _validate_model_identity(model, model_ids, counts)
        parsed, host = _validate_model_transport(model, allowed)
        locality, is_literal, is_dns = _model_locality(host)
        literal_private_model_count += int(is_literal)
        private_dns_model_count += int(is_dns)
        auth_modes = _model_auth_modes(model)
        canonical.append(
            {
                "modelIdentityDigest": _digest(model_id.encode("utf-8")),
                "class": level,
                "transport": locality,
                "scheme": parsed.scheme.casefold(),
                "authentication": "+".join(sorted(auth_modes)),
            }
        )
    if counts != {"light": 1, "normal": 1}:
        raise CertificationAssetError("runtime_model_registry_class_invalid")
    return {
        "digest": _digest(
            _canonical_bytes(sorted(canonical, key=lambda item: item["class"]))
        ),
        "modelCount": 2,
        "lightCount": 1,
        "normalCount": 1,
        "localPrivateTransportOnly": True,
        "referenceBackedCredentialsOnly": True,
        "literalPrivateModelCount": literal_private_model_count,
        "privateDnsModelCount": private_dns_model_count,
        "runtimePrivateResolutionRequired": True,
    }


def _resolved_model_host(model: Any) -> tuple[str, int | None]:
    try:
        parsed = urlsplit(str(getattr(model, "base_url", "") or ""))
        host = str(parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise CertificationAssetError("runtime_model_transport_invalid") from exc
    return host, port


def _resolved_dns_addresses(host: str, port: int | None, getaddrinfo: Any) -> set[str]:
    try:
        answers = getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CertificationAssetError("runtime_model_private_dns_unproven") from exc
    resolved: set[str] = set()
    for answer_count, answer in enumerate(answers, start=1):
        if answer_count > 64:
            raise CertificationAssetError(
                "runtime_model_private_dns_unproven"
            ) from None
        try:
            resolved.add(ipaddress.ip_address(str(answer[4][0])).compressed)
        except (IndexError, TypeError, ValueError) as exc:
            raise CertificationAssetError("runtime_model_private_dns_unproven") from exc
    return resolved


def _prove_private_dns_host(
    host: str, port: int | None, allowed: set[str], getaddrinfo: Any
) -> None:
    if not host or host not in allowed:
        raise CertificationAssetError("runtime_model_private_dns_unproven") from None
    resolved = _resolved_dns_addresses(host, port, getaddrinfo)
    if len(resolved) != 1:
        raise CertificationAssetError("runtime_model_private_dns_unproven") from None
    resolved_address = ipaddress.ip_address(next(iter(resolved)))
    if not (
        resolved_address.is_loopback
        or any(resolved_address in network for network in _PRIVATE_MODEL_NETWORKS)
    ):
        raise CertificationAssetError("runtime_model_private_dns_unproven") from None


def _prove_literal_private_host(address: Any) -> None:
    if not (
        address.is_loopback
        or any(address in network for network in _PRIVATE_MODEL_NETWORKS)
    ):
        raise CertificationAssetError("runtime_model_locality_unproven")


def prove_model_registry_runtime(
    models: list[Any],
    allowed_private_hosts: list[str],
    *,
    resolver: Any = None,
) -> dict[str, Any]:
    """Prove private DNS once; request transports independently pin and recheck it."""

    proof = derive_model_registry_proof(models, allowed_private_hosts)
    allowed = {
        str(host).strip().casefold().rstrip(".") for host in allowed_private_hosts
    }
    private_dns_count = 0
    literal_count = 0
    getaddrinfo = resolver or socket.getaddrinfo
    for model in models:
        host, port = _resolved_model_host(model)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            _prove_private_dns_host(host, port, allowed, getaddrinfo)
            private_dns_count += 1
        else:
            _prove_literal_private_host(address)
            literal_count += 1
    if (
        literal_count != proof["literalPrivateModelCount"]
        or private_dns_count != proof["privateDnsModelCount"]
    ):
        raise CertificationAssetError("runtime_model_transport_proof_mismatch")
    return {
        "modelCount": 2,
        "literalPrivateModelCount": literal_count,
        "privateDnsModelCount": private_dns_count,
        "privateDnsUniqueResolutionProven": True,
        "privateBoundaryProven": True,
        "dnsRebindingGuarded": True,
    }


def _validate_profile(
    payload: bytes,
    *,
    configuration_digest: str,
    model_registry_digest: str,
    identity_authority: dict[str, Any],
) -> None:
    value = _json_without_duplicates(payload, code="runtime_profile_invalid")
    expected = {
        "apiVersion": "graphos.io/v2",
        "kind": "SkillValidationRuntimeProfile",
        "configurationDigest": configuration_digest,
        "modelRegistryDigest": model_registry_digest,
        "identityAuthority": identity_authority,
        "engineTopology": "local-autostart",
        "observability": "metadata-only",
        "sequential": True,
    }
    if value != expected:
        raise CertificationAssetError("runtime_profile_invalid")


def _configuration_proof(payload: bytes) -> dict[str, Any]:
    root = _json_without_duplicates(payload, code="runtime_configuration_invalid")
    models, allowed = _model_configuration(root)
    return derive_model_registry_proof(models, allowed)


def _runtime_profile_document(configuration: bytes) -> dict[str, Any]:
    proof = _configuration_proof(configuration)
    identity_authority = _identity_authority_configuration(
        _json_without_duplicates(configuration, code="runtime_configuration_invalid")
    )
    return {
        "apiVersion": "graphos.io/v2",
        "kind": "SkillValidationRuntimeProfile",
        "configurationDigest": _digest(configuration),
        "modelRegistryDigest": proof["digest"],
        "identityAuthority": identity_authority,
        "engineTopology": "local-autostart",
        "observability": "metadata-only",
        "sequential": True,
    }


def generate_runtime_profile(
    *, configuration_reference: str, profile_reference: str
) -> dict[str, Any]:
    """Publish the deterministic runtime profile resolved through AgentConfig."""

    configuration_path = _material_path(
        configuration_reference,
        code="runtime_configuration_reference_invalid",
    )
    profile_path = _material_path(
        profile_reference,
        code="runtime_profile_reference_invalid",
    )
    configuration = _read_regular(
        configuration_path,
        limit=_MAX_MATERIAL_BYTES,
        code="runtime_configuration_invalid",
    )
    try:
        if configuration_path.resolve(strict=True) == profile_path.resolve(
            strict=False
        ):
            raise CertificationAssetError("runtime_profile_destination_invalid")
    except CertificationAssetError:
        raise
    except OSError as exc:
        raise CertificationAssetError("runtime_profile_destination_invalid") from exc
    profile = _runtime_profile_document(configuration)
    rendered = json.dumps(profile, sort_keys=True, indent=2) + "\n"
    from agent_utilities.skills.runtime_validation import publish_report

    publish_report(profile_path, rendered)
    if _read_regular(
        profile_path,
        limit=_MAX_MATERIAL_BYTES,
        code="runtime_profile_invalid",
    ) != rendered.encode("utf-8"):
        raise CertificationAssetError("runtime_profile_invalid")
    return profile


def load_runtime_materials(
    deployment: SkillValidationDeployment, *, require_active_configuration: bool
) -> dict[str, Any]:
    """Recompute and cross-verify exact runtime configuration/profile material."""

    configuration_path = _material_path(
        deployment.runtime.configuration_reference,
        code="runtime_configuration_reference_invalid",
    )
    profile_path = _material_path(
        deployment.runtime.profile_reference,
        code="runtime_profile_reference_invalid",
    )
    configuration = _read_regular(
        configuration_path,
        limit=_MAX_MATERIAL_BYTES,
        code="runtime_configuration_invalid",
    )
    profile = _read_regular(
        profile_path, limit=_MAX_MATERIAL_BYTES, code="runtime_profile_invalid"
    )
    if _digest(configuration) != deployment.runtime.configuration_digest:
        raise CertificationAssetError("runtime_configuration_digest_mismatch")
    if _digest(profile) != deployment.runtime.profile_digest:
        raise CertificationAssetError("runtime_profile_digest_mismatch")
    proof = _configuration_proof(configuration)
    root = _json_without_duplicates(configuration, code="runtime_configuration_invalid")
    models, model_private_hosts = _model_configuration(root)
    identity_authority = _identity_authority_configuration(root)
    expected_proof = deployment.runtime.model_registry.model_dump(by_alias=True)
    if proof != expected_proof:
        raise CertificationAssetError("runtime_model_registry_digest_mismatch")
    _validate_profile(
        profile,
        configuration_digest=deployment.runtime.configuration_digest,
        model_registry_digest=proof["digest"],
        identity_authority=identity_authority,
    )
    if identity_authority != deployment.identity_authority.model_dump(by_alias=True):
        raise CertificationAssetError("runtime_identity_authority_mismatch")
    if require_active_configuration:
        from agent_utilities.core.paths import config_dir

        active = config_dir() / "config.json"
        try:
            if not os.path.samefile(configuration_path, active):
                raise CertificationAssetError("runtime_configuration_not_active")
        except OSError as exc:
            raise CertificationAssetError("runtime_configuration_not_active") from exc
    return {
        "modelRegistry": proof,
        "identityAuthority": identity_authority,
        "models": models,
        "modelPrivateHosts": model_private_hosts,
    }


def verify_release_bindings(
    deployment: SkillValidationDeployment,
) -> dict[str, Any]:
    """Verify spec, signed promotion evidence, and installed artifact digests."""

    specification_path = _material_path(
        deployment.release.specification_reference,
        code="release_specification_reference_invalid",
    )
    evidence_path = _material_path(
        deployment.release.promotion_evidence_reference,
        code="promotion_evidence_reference_invalid",
    )
    specification = _read_regular(
        specification_path,
        limit=_MAX_MATERIAL_BYTES,
        code="release_specification_invalid",
    )
    evidence_payload = _read_regular(
        evidence_path,
        limit=_MAX_EVIDENCE_BYTES,
        code="promotion_evidence_invalid",
    )
    if _digest(specification) != deployment.release.specification_digest:
        raise CertificationAssetError("release_specification_digest_mismatch")
    if _digest(evidence_payload) != deployment.release.promotion_evidence_digest:
        raise CertificationAssetError("promotion_evidence_digest_mismatch")
    try:
        from scripts.release.promote_local_release import verify_evidence_file

        evidence = verify_evidence_file(
            spec_path=specification_path,
            release_id=deployment.release.id,
            evidence_path=evidence_path,
        )
    except Exception as exc:
        raise CertificationAssetError("promotion_evidence_verification_failed") from exc
    certification = evidence.get("certificationArtifacts")
    if evidence.get("status") != "promoted" or not isinstance(certification, dict):
        raise CertificationAssetError("promotion_evidence_not_promoted")
    if evidence.get("specDigest") != deployment.release.specification_digest:
        raise CertificationAssetError("promotion_specification_binding_mismatch")
    if _promotion_certification_binding(certification) != {
        "agentUtilitiesSha256": deployment.release.agent_utilities_sha256,
        "agentUtilitiesFileCount": deployment.release.agent_utilities_file_count,
        "distributionClosureSha256": deployment.release.distribution_closure_sha256,
        "releasePythonSha256": deployment.release.release_python_sha256,
        "graphOsDigest": deployment.release.graph_os_digest,
        "engineDigest": deployment.release.engine_digest,
    }:
        raise CertificationAssetError("promotion_artifact_binding_mismatch")
    return evidence


def _promotion_certification_binding(
    certification: dict[str, Any],
) -> dict[str, Any]:
    count = certification.get("agentUtilitiesFileCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 10:
        raise CertificationAssetError("promotion_artifact_binding_invalid")
    raw_fields = {
        "agentUtilitiesSha256": "agentUtilitiesSha256",
        "distributionClosureSha256": "distributionClosureSha256",
        "releasePythonSha256": "releasePythonSha256",
        "graphOsDigest": "graphosSha256",
        "engineDigest": "engineSha256",
    }
    binding = {
        target: "sha256:" + str(certification.get(source) or "")
        for target, source in raw_fields.items()
    }
    if any(_DIGEST.fullmatch(value) is None for value in binding.values()):
        raise CertificationAssetError("promotion_artifact_binding_invalid")
    return {**binding, "agentUtilitiesFileCount": count}


def _is_regular_unhardlinked(metadata: os.stat_result) -> bool:
    return (
        not stat.S_ISLNK(metadata.st_mode)
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
    )


def _is_same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _is_canonical_graph_os_path(canonical: Path) -> bool:
    return (
        canonical.name == "graph-os"
        and canonical.parent.name == "bin"
        and canonical.parent.parent.name == "runtime"
    )


def _is_invalid_executable_layout(
    start_executable: Path,
    original: os.stat_result,
    canonical: Path,
    canonical_metadata: os.stat_result,
) -> bool:
    if not start_executable.is_absolute():
        return True
    if not _is_regular_unhardlinked(original):
        return True
    if not _is_regular_unhardlinked(canonical_metadata):
        return True
    if not _is_same_inode(original, canonical_metadata):
        return True
    return not _is_canonical_graph_os_path(canonical)


def _validated_start_executable_layout(
    start_executable: Path,
) -> tuple[os.stat_result, Path, os.stat_result]:
    try:
        original = start_executable.lstat()
        canonical = start_executable.resolve(strict=True)
        canonical_metadata = canonical.lstat()
    except OSError as exc:
        raise CertificationAssetError("installed_release_layout_invalid") from exc
    if _is_invalid_executable_layout(
        start_executable, original, canonical, canonical_metadata
    ):
        raise CertificationAssetError("installed_release_layout_invalid")
    return original, canonical, canonical_metadata


def _confirm_executable_unchanged(
    start_executable: Path, original: os.stat_result
) -> None:
    try:
        after = start_executable.stat(follow_symlinks=False)
    except OSError as exc:
        raise CertificationAssetError("installed_release_attestation_failed") from exc
    if _stat_signature(original) != _stat_signature(after):
        raise CertificationAssetError("installed_release_attestation_failed")


def attest_installed_release_binding(
    deployment: SkillValidationDeployment,
    *,
    start_executable: Path,
    promotion_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Recompute the exact sealed release containing the selected GraphOS."""

    original, canonical, _canonical_metadata = _validated_start_executable_layout(
        start_executable
    )
    release_root = canonical.parent.parent.parent
    certification = promotion_evidence.get("certificationArtifacts")
    if not isinstance(certification, dict):
        raise CertificationAssetError("promotion_evidence_not_promoted")
    expected = _promotion_certification_binding(certification)
    try:
        from scripts.release.promote_local_release import attest_installed_release

        installed = attest_installed_release(release_root)
    except Exception as exc:
        raise CertificationAssetError("installed_release_attestation_failed") from exc
    _confirm_executable_unchanged(start_executable, original)
    actual = _promotion_certification_binding(installed)
    deployment_binding = {
        "agentUtilitiesSha256": deployment.release.agent_utilities_sha256,
        "agentUtilitiesFileCount": deployment.release.agent_utilities_file_count,
        "distributionClosureSha256": deployment.release.distribution_closure_sha256,
        "releasePythonSha256": deployment.release.release_python_sha256,
        "graphOsDigest": deployment.release.graph_os_digest,
        "engineDigest": deployment.release.engine_digest,
    }
    if actual != expected or actual != deployment_binding:
        raise CertificationAssetError("installed_release_attestation_mismatch")
    return actual
