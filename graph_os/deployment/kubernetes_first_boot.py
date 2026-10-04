"""Read-only Kubernetes observations; never a first-boot acceptance authority.

Invoke with ``python -m graph_os.deployment.kubernetes_first_boot``. The profile
loader owns configuration validation. This module compares rendered JSON resources
with a bounded observation snapshot; it neither installs tools nor applies resources.
Application evidence is supplied, not authenticated or fabricated by this verifier.
Collection trusts the operator's PATH and inherited kubeconfig/environment;
kubeconfig credential plugins can execute locally even for a read-only API GET.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

import anyio
from anyio.abc import Process

from .genesis_environments import (
    EnvironmentProfile,
    EnvironmentProfileError,
    load_environment_profile,
)

MAX_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 1024
TIMEOUT_SECONDS = 20
CLEANUP_SECONDS = 2
_RESOURCE_TYPES = "deployments,statefulsets,replicasets,pods,persistentvolumeclaims"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_APPLICATION_CHECKS = ("engine", "identity", "administrator", "secrets")


class EvidenceError(ValueError):
    """A bounded refusal code, never raw cluster output or credential material."""


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("malformed_observation")
    return value


def _items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise EvidenceError("invalid_inventory")
    return [_mapping(item) for item in value]


def _decode(raw: bytes) -> Any:
    if len(raw) > MAX_BYTES:
        raise EvidenceError("observation_too_large")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise EvidenceError("invalid_json") from error


def _read(path: Path) -> Any:
    with path.open("rb") as stream:
        return _decode(stream.read(MAX_BYTES + 1))


async def _close_process(process: Process) -> None:
    try:
        _kill_process_group(process)
    finally:
        await _reap_process(process)


def _kill_process_group(process: Process) -> None:
    # Linux collection starts a private session so inherited credential-plugin
    # children cannot keep our output pipe open after timeout or cancellation.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError as error:
        if process.returncode is None:
            raise EvidenceError("collection_cleanup_uncertain") from error


async def _reap_process(process: Process) -> None:
    # Cancellation must still reap the child, but cannot introduce an unbounded wait.
    with anyio.move_on_after(CLEANUP_SECONDS, shield=True) as cleanup:
        if process.stdout is not None:
            await process.stdout.aclose()
        await process.wait()
    if cleanup.cancel_called:
        raise EvidenceError("collection_cleanup_timeout")
    # Do not call Process.aclose(): implementations may shield an unbounded wait.
    # wait() reaps the child; the only owned pipe was explicitly closed above.


async def _stream_output(process: Process) -> bytes:
    if process.stdout is None:
        raise EvidenceError("collection_unavailable")
    retained = bytearray()
    while True:
        try:
            chunk = await process.stdout.receive(
                min(65536, MAX_BYTES - len(retained) + 1)
            )
        except anyio.EndOfStream:
            break
        if len(chunk) > MAX_BYTES - len(retained):
            raise EvidenceError("observation_too_large")
        retained.extend(chunk)
    if await process.wait() != 0:
        raise EvidenceError("collection_failed")
    return bytes(retained)


async def _collect_process(argv: list[str]) -> bytes:
    # PATH and kubeconfig are operator-trusted deployment inputs. Environment is
    # inherited, including kubeconfig credential plugins that can execute locally.
    # Resolve once to an absolute executable; this is not an environment sandbox.
    executable = shutil.which("kubectl")
    if executable is None:
        raise EvidenceError("collection_unavailable")
    command = [str(Path(executable).resolve()), *argv[1:]]
    process = None
    try:
        with anyio.fail_after(TIMEOUT_SECONDS):
            async with (
                await anyio.open_file(os.devnull, "rb") as input_null,
                await anyio.open_file(os.devnull, "wb") as error_null,
            ):
                process = await anyio.open_process(
                    command,
                    stdin=input_null.wrapped,
                    stderr=error_null.wrapped,
                    start_new_session=True,
                )
            return await _stream_output(process)
    except TimeoutError as error:
        raise EvidenceError("collection_timeout") from error
    finally:
        if process is not None:
            await _close_process(process)


def _run_readonly(argv: list[str]) -> bytes:
    return anyio.run(_collect_process, argv)


def collect_observations(
    profile: EnvironmentProfile,
    runner: Callable[[list[str]], bytes] = _run_readonly,
) -> Mapping[str, Any]:
    """Issue one explicit-context/namespace GET; never query Secret values."""
    if profile.target.orchestrator != "kubernetes":
        raise EvidenceError("unsupported_target")
    context = profile.target.cluster_context_ref
    namespace = profile.target.namespace
    if (
        not isinstance(context, str)
        or not context
        or context.startswith("-")
        or any(ord(c) < 32 for c in context)
        or not isinstance(namespace, str)
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", namespace)
    ):
        raise EvidenceError("explicit_target_required")
    command = [
        "kubectl",
        "--context",
        context,
        "--namespace",
        namespace,
        "--request-timeout=15s",
        "get",
        _RESOURCE_TYPES,
        "-o",
        "json",
    ]
    try:
        return _mapping(_decode(runner(command)))
    except (OSError, TimeoutError) as error:
        raise EvidenceError("collection_unavailable") from error


def snapshot_digest(
    profile: EnvironmentProfile, rendered: Sequence[Mapping[str, Any]]
) -> str:
    """Bind supplied evidence to the selected profile and exact rendered inventory."""
    material = asdict(profile)
    material.pop("source")
    raw = json.dumps([material, rendered], sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(item.get("metadata"))


def _key(item: Mapping[str, Any]) -> tuple[str, str]:
    kind, name = item.get("kind"), _metadata(item).get("name")
    if not isinstance(kind, str) or not isinstance(name, str) or not name:
        raise EvidenceError("invalid_resource_identity")
    return kind, name


def _inventory(
    items: list[Mapping[str, Any]], namespace: str, *, observed: bool = False
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {}
    for item in items:
        if observed:
            _identity(_metadata(item))
        if (
            _metadata(item).get("namespace", None if observed else namespace)
            != namespace
        ):
            raise EvidenceError("namespace_mismatch")
        key = _key(item)
        if key in result:
            raise EvidenceError("duplicate_resource")
        result[key] = item
    return result


def _identity(metadata: Mapping[str, Any]) -> None:
    for field in ("name", "uid"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise EvidenceError("invalid_resource_identity")
    for ref in _items(metadata.get("ownerReferences", [])):
        _identity({"name": ref.get("name"), "uid": ref.get("uid")})
        if (
            not isinstance(ref.get("kind"), str)
            or type(ref.get("controller", False)) is not bool
        ):
            raise EvidenceError("invalid_owner_identity")


def _owned(item: Mapping[str, Any], owner: Mapping[str, Any]) -> bool:
    uid = _metadata(owner).get("uid")
    refs = _items(_metadata(item).get("ownerReferences", []))
    return bool(uid) and any(
        ref.get("uid") == uid
        and ref.get("kind") == owner.get("kind")
        and ref.get("name") == _metadata(owner).get("name")
        and ref.get("controller") is True
        for ref in refs
    )


def _pods(
    workload: Mapping[str, Any], inventory: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    owners = [workload]
    if workload["kind"] == "Deployment":
        owners = [
            item
            for (kind, _), item in inventory.items()
            if kind == "ReplicaSet" and _owned(item, workload)
        ]
    return [
        item
        for (kind, _), item in inventory.items()
        if kind == "Pod" and any(_owned(item, owner) for owner in owners)
    ]


def _image_digest(image: Any) -> str:
    if not isinstance(image, str):
        raise EvidenceError("missing_image_digest")
    digest = (
        image.rsplit("@", 1)[-1].removeprefix("docker://").removeprefix("containerd://")
    )
    if not _DIGEST.fullmatch(digest):
        raise EvidenceError("missing_image_digest")
    return digest


def _contains(expected: Any, actual: Any) -> bool:
    """Permit API defaults without dropping any rendered configuration constraint."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _contains(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_contains(x, y) for x, y in zip(expected, actual, strict=True))
        )
    return type(expected) is type(actual) and expected == actual


def _by_name(items: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise EvidenceError("invalid_container_identity")
        result[name] = item
    return result


def _container_ready(
    container: Mapping[str, Any], current: Mapping[str, Any], *, init: bool
) -> bool:
    if _image_digest(current.get("imageID")) != _image_digest(container.get("image")):
        return False
    state = _mapping(current.get("state"))
    if type(current.get("ready")) is not bool:
        return False
    if init and container.get("restartPolicy") != "Always":
        return _terminated_container_ready(state)
    return _running_container_ready(current, state)


def _terminated_container_ready(state: Mapping[str, Any]) -> bool:
    terminated = _mapping(state.get("terminated", {}))
    return (
        set(state) == {"terminated"}
        and type(terminated.get("exitCode")) is int
        and terminated["exitCode"] == 0
    )


def _running_container_ready(
    current: Mapping[str, Any], state: Mapping[str, Any]
) -> bool:
    return (
        current.get("ready") is True
        and set(state) == {"running"}
        and isinstance(state["running"], dict)
        and set(state["running"]) <= {"startedAt"}
        and all(isinstance(v, str) for v in state["running"].values())
    )


def _container_group(
    expected: Mapping[str, Any], pod: Mapping[str, Any], field: str, status_field: str
) -> bool:
    wanted = _by_name(_items(expected.get(field, [])))
    actual = _by_name(_items(_mapping(pod.get("spec")).get(field, [])))
    statuses = _by_name(_items(_mapping(pod.get("status")).get(status_field, [])))
    if wanted.keys() != actual.keys() or wanted.keys() != statuses.keys():
        return False
    return all(
        _container_configuration(container, actual[name], _mapping(pod.get("spec")))
        and _container_ready(container, statuses[name], init=field == "initContainers")
        for name, container in wanted.items()
    )


def _mounts(container: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {}
    paths = set()
    for mount in _items(container.get("volumeMounts", [])):
        name, path = mount.get("name"), mount.get("mountPath")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(path, str)
            or not path
            or path in paths
        ):
            raise EvidenceError("invalid_mount_identity")
        paths.add(path)
        result[name, path] = mount
    return result


def _api_service_account_mount(
    mount: Mapping[str, Any], volumes: Mapping[str, Mapping[str, Any]]
) -> bool:
    name = mount.get("name")
    if not isinstance(name, str) or not re.fullmatch(
        r"kube-api-access-[a-z0-9]{5}", name
    ):
        return False
    canonical_mount = {
        "name": name,
        "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
        "readOnly": True,
    }
    if not _contains(canonical_mount, mount) or not _contains(mount, canonical_mount):
        return False
    volume = volumes.get(name)
    if volume is None:
        return False
    projected = _mapping(volume.get("projected"))
    sources = _items(projected.get("sources"))
    if len(sources) != 3:
        return False
    token = _mapping(sources[0].get("serviceAccountToken"))
    expiration = token.get("expirationSeconds")
    if type(expiration) is not int or not 600 <= expiration <= 86400:
        return False
    canonical = {
        "name": name,
        "projected": {
            "defaultMode": 420,
            "sources": [
                {
                    "serviceAccountToken": {
                        "expirationSeconds": expiration,
                        "path": "token",
                    }
                },
                {
                    "configMap": {
                        "name": "kube-root-ca.crt",
                        "items": [{"key": "ca.crt", "path": "ca.crt"}],
                    }
                },
                {
                    "downwardAPI": {
                        "items": [
                            {
                                "path": "namespace",
                                "fieldRef": {
                                    "apiVersion": "v1",
                                    "fieldPath": "metadata.namespace",
                                },
                            }
                        ]
                    }
                },
            ],
        },
    }
    return _contains(canonical, volume) and _contains(volume, canonical)


def _container_configuration(
    expected: Mapping[str, Any], actual: Mapping[str, Any], pod_spec: Mapping[str, Any]
) -> bool:
    constraints = {k: v for k, v in expected.items() if k != "volumeMounts"}
    if not _contains(constraints, actual):
        return False
    wanted, observed = _mounts(expected), _mounts(actual)
    if not all(_contains(mount, observed.get(key)) for key, mount in wanted.items()):
        return False
    volumes = _by_name(_items(pod_spec.get("volumes", [])))
    additions = observed.keys() - wanted.keys()
    if additions and pod_spec.get("automountServiceAccountToken") is False:
        return False
    return all(_api_service_account_mount(observed[key], volumes) for key in additions)


def _container_checks(expected: Mapping[str, Any], pod: Mapping[str, Any]) -> bool:
    return bool(expected.get("containers")) and all(
        _container_group(expected, pod, field, statuses)
        for field, statuses in [
            ("containers", "containerStatuses"),
            ("initContainers", "initContainerStatuses"),
        ]
    )


def _quantity(value: Any) -> Decimal:
    """Parse positive Kubernetes storage quantities without float rounding."""
    if not isinstance(value, str) or len(value) > 80:
        raise EvidenceError("invalid_storage_quantity")
    match = re.fullmatch(
        r"(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d{1,3}|[EPTGMK]i|[EPTGMkmun])?", value
    )
    if match is None:
        raise EvidenceError("invalid_storage_quantity")
    number, suffix = match.groups()
    with localcontext() as context:
        context.prec = 128
        return _scaled_quantity(number, suffix)


def _scaled_quantity(number: str, suffix: str | None) -> Decimal:
    try:
        quantity = Decimal(number)
        if suffix and suffix.endswith("i"):
            quantity *= 1024 ** ("KMGTPE".index(suffix[0]) + 1)
        elif suffix and len(suffix) > 1 and suffix[0] in "eE":
            quantity *= Decimal(10) ** int(suffix[1:])
        elif suffix:
            quantity *= (
                Decimal(10)
                ** {
                    "n": -9,
                    "u": -6,
                    "m": -3,
                    "k": 3,
                    "M": 6,
                    "G": 9,
                    "T": 12,
                    "P": 15,
                    "E": 18,
                }[suffix]
            )
    except (InvalidOperation, ValueError) as error:
        raise EvidenceError("invalid_storage_quantity") from error
    if quantity <= 0 or quantity > 2**63 - 1:
        raise EvidenceError("invalid_storage_quantity")
    return quantity


def _claim_ready(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    if _metadata(actual).get("deletionTimestamp") is not None:
        return False
    spec, wanted = _mapping(actual.get("spec")), _mapping(expected.get("spec"))
    status = _mapping(actual.get("status"))
    request = _quantity(
        _mapping(_mapping(spec.get("resources")).get("requests")).get("storage")
    )
    required = _quantity(
        _mapping(_mapping(wanted.get("resources")).get("requests")).get("storage")
    )
    # Storage request spellings may be API-canonicalized (1Gi == 1024Mi).
    comparable = dict(wanted)
    resources = dict(_mapping(comparable.pop("resources")))
    requests = dict(_mapping(resources.pop("requests")))
    requests.pop("storage")
    return (
        status.get("phase") == "Bound"
        and request == required
        and _quantity(_mapping(status.get("capacity")).get("storage")) >= required
        and _contains(comparable, spec)
        and _contains(resources, spec["resources"])
        and _contains(requests, spec["resources"]["requests"])
    )


def _storage_checks(
    expected: Mapping[str, Any],
    pod: Mapping[str, Any],
    inventory: Mapping[tuple[str, str], Mapping[str, Any]],
) -> bool:
    volumes = _items(_mapping(pod.get("spec")).get("volumes", []))
    observed = _by_name(volumes)
    for volume_name, volume in _by_name(_items(expected.get("volumes", []))).items():
        if not _contains(volume, observed.get(volume_name)):
            return False
    for volume in volumes:
        if "persistentVolumeClaim" not in volume:
            continue
        name = _mapping(volume["persistentVolumeClaim"]).get("claimName")
        if not isinstance(name, str) or not name:
            return False
        pvc = inventory.get(("PersistentVolumeClaim", name))
        if pvc is None or not _claim_ready(pvc, pvc):
            return False
    return True


def _generation_current(actual: Mapping[str, Any]) -> bool:
    generation = _metadata(actual).get("generation")
    status = _mapping(actual.get("status"))
    return (
        type(generation) is int
        and generation >= 1
        and type(status.get("observedGeneration")) is int
        and status["observedGeneration"] == generation
    )


def _workload_configuration(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    replicas = spec.get("replicas", 1)
    actual_spec = _mapping(actual.get("spec"))
    return (
        _metadata(actual).get("deletionTimestamp") is None
        and type(replicas) is int
        and replicas >= 1
        and type(actual_spec.get("replicas", 1)) is int
        and actual_spec.get("replicas", 1) == replicas
        and _contains(spec, actual_spec)
    )


def _workload_ready(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    inventory: Mapping[tuple[str, str], Mapping[str, Any]],
) -> bool:
    spec = _mapping(expected.get("spec"))
    if not _workload_configuration(spec, actual) or not _generation_current(actual):
        return False
    replicas = spec.get("replicas", 1)
    status = _mapping(actual.get("status"))
    pods = _pods(actual, inventory)
    return (
        len(pods) == replicas
        and type(status.get("readyReplicas")) is int
        and status["readyReplicas"] == replicas
        and all(_pod_ready(spec, pod, inventory) for pod in pods)
    )


def _pod_state_ready(pod: Mapping[str, Any]) -> bool:
    state = _mapping(pod.get("status"))
    conditions = _items(state.get("conditions", []))
    if any(
        not isinstance(c.get("type"), str)
        or c.get("status") not in ("True", "False", "Unknown")
        for c in conditions
    ):
        return False
    ready = [c for c in conditions if c.get("type") == "Ready"]
    if len(ready) != 1 or ready[0].get("status") != "True":
        return False
    return (
        _metadata(pod).get("deletionTimestamp") is None
        and state.get("phase") == "Running"
    )


def _pod_storage_template(
    spec: Mapping[str, Any],
    pod: Mapping[str, Any],
    inventory: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Mapping[str, Any]:
    template = _mapping(_mapping(spec.get("template")).get("spec"))
    pod_template = dict(template)
    volumes = list(_items(template.get("volumes", [])))
    for claim in _items(spec.get("volumeClaimTemplates", [])):
        name = _metadata(claim).get("name")
        if not isinstance(name, str) or not name:
            raise EvidenceError("invalid_claim_template")
        claim_name = f"{name}-{_metadata(pod)['name']}"
        actual_claim = inventory.get(("PersistentVolumeClaim", claim_name))
        if actual_claim is None or not _claim_ready(claim, actual_claim):
            raise EvidenceError("invalid_claim_template")
        volumes.append(
            {
                "name": name,
                "persistentVolumeClaim": {"claimName": claim_name},
            }
        )
    pod_template["volumes"] = volumes
    return pod_template


def _pod_ready(
    spec: Mapping[str, Any],
    pod: Mapping[str, Any],
    inventory: Mapping[tuple[str, str], Mapping[str, Any]],
) -> bool:
    if not _pod_state_ready(pod):
        return False
    template = _mapping(_mapping(spec.get("template")).get("spec"))
    pod_template = _pod_storage_template(spec, pod, inventory)
    # API additions (nodeName, DNS defaults, projected service-account volumes)
    # may be absent from the render. Every rendered pod constraint must survive.
    # Container checks own mount identity and the narrowly validated API injection;
    # applying recursive list equality here would reject that same valid injection.
    constraints = {
        k: v
        for k, v in template.items()
        if k not in {"volumes", "containers", "initContainers"}
    }
    actual_spec = _mapping(pod.get("spec"))
    rendered_metadata = _mapping(_mapping(spec.get("template")).get("metadata", {}))
    return (
        _contains(constraints, actual_spec)
        and _contains(rendered_metadata, _metadata(pod))
        and _container_checks(template, pod)
        and _storage_checks(pod_template, pod, inventory)
    )


def _declared_claim_checks(
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    observed: Mapping[tuple[str, str], Mapping[str, Any]],
    checks: dict[str, bool],
) -> None:
    for index, ((kind, name), claim) in enumerate(expected.items()):
        if kind == "PersistentVolumeClaim":
            actual_claim = observed.get((kind, name))
            checks[f"claim_{index}"] = actual_claim is not None and _claim_ready(
                claim, actual_claim
            )


def _workload_checks(
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    observed: Mapping[tuple[str, str], Mapping[str, Any]],
    checks: dict[str, bool],
) -> None:
    workloads = [
        v for (kind, _), v in expected.items() if kind in {"Deployment", "StatefulSet"}
    ]
    checks["workloads_present"] = bool(workloads)
    for index, workload in enumerate(workloads):
        actual = observed.get(_key(workload))
        checks[f"workload_{index}"] = actual is not None and _workload_ready(
            workload, actual, observed
        )


def _infrastructure_checks(
    profile: EnvironmentProfile,
    rendered: list[Mapping[str, Any]],
    observations: Mapping[str, Any],
    checks: dict[str, bool],
) -> None:
    if profile.target.orchestrator != "kubernetes":
        raise EvidenceError("unsupported_target")
    expected = _inventory(_items(rendered), profile.target.namespace)
    observed = _inventory(
        _items(observations.get("items")), profile.target.namespace, observed=True
    )
    if any(
        kind in {"Pod", "DaemonSet", "Job", "CronJob", "ReplicaSet"}
        for kind, _ in expected
    ):
        raise EvidenceError("unsupported_workload")
    _declared_claim_checks(expected, observed, checks)
    _workload_checks(expected, observed, checks)


def verify_first_boot(
    profile: EnvironmentProfile,
    rendered: list[Mapping[str, Any]],
    observations: Mapping[str, Any],
    application_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate supplied observations; even a complete fixture is not live acceptance."""
    checks: dict[str, bool] = {}
    digest = snapshot_digest(profile, rendered)
    try:
        _infrastructure_checks(profile, rendered, observations, checks)
    except (EvidenceError, KeyError, TypeError, ValueError, RecursionError):
        checks["valid_observations"] = False
    evidence = application_evidence or {}
    bound = evidence.get("snapshot_digest") == digest
    supplied = evidence.get("checks", {})
    application = {
        name: bound and isinstance(supplied, dict) and supplied.get(name) is True
        for name in _APPLICATION_CHECKS
    }
    return {
        "snapshot_digest": digest,
        "infrastructure_ready": bool(checks) and all(checks.values()),
        "infrastructure_checks": checks,
        "application_evidence": application,
        "application_evidence_status": "supplied"
        if all(application.values())
        else "blocked",
        "acceptance": "not_qualified",
        "privacy_safe": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--rendered", type=Path, required=True, help="Rendered resource JSON array"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--observations", type=Path)
    source.add_argument(
        "--collect",
        action="store_true",
        help="Read the explicit profile cluster target",
    )
    parser.add_argument("--application-evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        profile = load_environment_profile(args.profile)
        rendered = _items(_read(args.rendered))
        observations = (
            _mapping(_read(args.observations))
            if args.observations
            else collect_observations(profile)
        )
        evidence = (
            _mapping(_read(args.application_evidence))
            if args.application_evidence
            else None
        )
        report = verify_first_boot(profile, rendered, observations, evidence)
    except (OSError, ValueError, RuntimeError, EnvironmentProfileError):
        report = {
            "infrastructure_ready": False,
            "acceptance": "not_qualified",
            "error": "evidence_unavailable",
            "privacy_safe": True,
        }
    print(json.dumps(report, sort_keys=True))
    return (
        0
        if report["infrastructure_ready"]
        and report.get("application_evidence_status") == "supplied"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
