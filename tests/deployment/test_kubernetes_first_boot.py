"""First-boot observation fixtures prove verifier behavior, never live acceptance."""

from __future__ import annotations

import copy
import io
import json
import subprocess
from dataclasses import replace

import anyio
import pytest

from graph_os.deployment import kubernetes_first_boot as boot
from graph_os.deployment.genesis_environments import load_environment_profile

IMAGE = "example.invalid/graphos@sha256:" + "a" * 64
CLAIM_SPEC = {
    "storageClassName": "fixture-storage",
    "accessModes": ["ReadWriteOnce"],
    "resources": {"requests": {"storage": "1Gi"}},
}


@pytest.fixture
def profile():
    value = load_environment_profile("dev")
    return replace(
        value,
        target=replace(
            value.target, namespace="fixture", cluster_context_ref="fixture-context"
        ),
    )


def resource(kind, name, **fields):
    return {
        "kind": kind,
        "metadata": {"name": name, "namespace": "fixture", "uid": name + "-uid"},
        **fields,
    }


def owner(item):
    return {
        "kind": item["kind"],
        "name": item["metadata"]["name"],
        "uid": item["metadata"]["uid"],
        "controller": True,
    }


@pytest.fixture
def snapshot():
    container = {
        "name": "graphos",
        "image": IMAGE,
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
    }
    spec = {
        "containers": [container],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}],
    }
    expected = resource(
        "Deployment", "host", spec={"replicas": 1, "template": {"spec": spec}}
    )
    workload = copy.deepcopy(expected)
    workload["metadata"]["generation"] = 2
    workload["status"] = {"observedGeneration": 2, "readyReplicas": 1}
    rs = resource("ReplicaSet", "host-rs")
    rs["metadata"]["ownerReferences"] = [owner(workload)]
    pod = resource(
        "Pod",
        "host-pod",
        spec=copy.deepcopy(spec),
        status={
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "graphos",
                    "imageID": "docker-pullable://" + IMAGE,
                    "ready": True,
                    "state": {"running": {}},
                }
            ],
        },
    )
    pod["metadata"]["ownerReferences"] = [owner(rs)]
    pvc = resource(
        "PersistentVolumeClaim",
        "data",
        spec=copy.deepcopy(CLAIM_SPEC),
        status={"phase": "Bound", "capacity": {"storage": "1Gi"}},
    )
    return [
        expected,
        resource("PersistentVolumeClaim", "data", spec=copy.deepcopy(CLAIM_SPEC)),
    ], {"items": [workload, rs, pod, pvc]}


def test_valid_observations_require_separate_application_evidence(profile, snapshot):
    expected, observed = snapshot
    result = boot.verify_first_boot(profile, expected, observed)
    assert result["infrastructure_ready"] is True
    assert result["application_evidence_status"] == "blocked"
    assert result["acceptance"] == "not_qualified"
    evidence = {
        "snapshot_digest": result["snapshot_digest"],
        "checks": {k: True for k in ("engine", "identity", "administrator", "secrets")},
    }
    result = boot.verify_first_boot(profile, expected, observed, evidence)
    assert result["application_evidence_status"] == "supplied"
    assert result["acceptance"] == "not_qualified"
    evidence["checks"]["identity"] = "true"
    assert (
        boot.verify_first_boot(profile, expected, observed, evidence)[
            "application_evidence_status"
        ]
        == "blocked"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "pending",
        "crashloop",
        "missing_pvc",
        "unbound_pvc",
        "wrong_image",
        "wrong_digest",
        "missing_status",
        "wrong_owner",
        "wrong_namespace",
        "stale_generation",
        "duplicate",
        "no_workload",
        "terminating",
        "extra_pod",
        "ephemeral",
        "missing_mount",
    ],
)
def test_bad_cluster_observations_fail_closed(profile, snapshot, failure):
    expected, observed = snapshot
    _damage_container_or_storage(snapshot, failure)
    _damage_identity_or_lifecycle(snapshot, failure)
    assert (
        boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]
        is False
    )


def _damage_container_or_storage(snapshot, failure):
    expected, observed = snapshot
    workload, rs, pod, pvc = observed["items"]
    if failure == "pending":
        pod["status"]["phase"] = "Pending"
    elif failure == "crashloop":
        pod["status"]["containerStatuses"][0]["state"] = {
            "waiting": {"reason": "CrashLoopBackOff"}
        }
    elif failure == "missing_pvc":
        observed["items"].remove(pvc)
    elif failure == "unbound_pvc":
        pvc["status"]["phase"] = "Pending"
    elif failure == "wrong_image":
        pod["spec"]["containers"][0]["image"] = IMAGE.replace("a" * 64, "b" * 64)
    elif failure == "wrong_digest":
        pod["status"]["containerStatuses"][0]["imageID"] = (
            "containerd://sha256:" + "b" * 64
        )
    elif failure == "missing_status":
        pod["status"]["containerStatuses"] = []
    elif failure == "wrong_owner":
        rs["metadata"]["ownerReferences"][0]["uid"] = "imposter"


def _damage_identity_or_lifecycle(snapshot, failure):
    expected, observed = snapshot
    workload, rs, pod = observed["items"][:3]
    if failure == "wrong_namespace":
        pod["metadata"]["namespace"] = "other"
    elif failure == "stale_generation":
        workload["status"]["observedGeneration"] = 1
    elif failure == "duplicate":
        observed["items"].append(copy.deepcopy(pod))
    elif failure == "no_workload":
        expected.clear()
    elif failure == "terminating":
        pod["metadata"]["deletionTimestamp"] = "now"
    elif failure == "extra_pod":
        extra = copy.deepcopy(pod)
        extra["metadata"]["name"] = "another"
        observed["items"].append(extra)
    elif failure == "ephemeral":
        pod["spec"]["volumes"] = [{"name": "data", "emptyDir": {}}]
    elif failure == "missing_mount":
        pod["spec"]["containers"][0]["volumeMounts"] = []


def test_api_defaults_do_not_mask_drift(profile, snapshot):
    expected, observed = snapshot
    observed["items"][0]["spec"]["template"]["spec"]["containers"][0][
        "terminationMessagePath"
    ] = "/dev/termination-log"
    observed["items"][2]["spec"]["containers"][0]["terminationMessagePath"] = (
        "/dev/termination-log"
    )
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]


def test_native_sidecar_readiness_is_required(profile, snapshot):
    expected, observed = snapshot
    sidecar = {"name": "engine", "image": IMAGE, "restartPolicy": "Always"}
    for spec in [
        expected[0]["spec"]["template"]["spec"],
        observed["items"][0]["spec"]["template"]["spec"],
        observed["items"][2]["spec"],
    ]:
        spec["initContainers"] = [copy.deepcopy(sidecar)]
    statuses = [
        {"name": "engine", "imageID": IMAGE, "ready": True, "state": {"running": {}}}
    ]
    observed["items"][2]["status"]["initContainerStatuses"] = statuses
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]
    statuses[0]["ready"] = False
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def stateful_snapshot(snapshot, pod_name):
    expected, observed = snapshot
    workload, rs, pod, claim = observed["items"]
    for item in (expected[0], workload):
        item["kind"] = "StatefulSet"
        item["spec"]["template"]["spec"]["volumes"] = []
        item["spec"]["volumeClaimTemplates"] = [
            {"metadata": {"name": "data"}, "spec": copy.deepcopy(CLAIM_SPEC)}
        ]
    expected.pop(1)
    pod["metadata"]["name"] = pod_name
    pod["metadata"]["ownerReferences"] = [owner(workload)]
    claim_name = "data-" + pod_name
    claim["metadata"]["name"] = claim_name
    pod["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = claim_name
    observed["items"].remove(rs)
    return expected, observed, claim


def test_shared_engine_claim_templates_require_the_observed_claim(profile, snapshot):
    expected, observed, pvc = stateful_snapshot(snapshot, "host-0")
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]
    observed["items"].remove(pvc)
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def test_readonly_collector_has_explicit_scope_and_no_secret_reads(profile):
    commands = []

    def runner(command):
        commands.append(command)
        return b'{"items": []}'

    assert boot.collect_observations(profile, runner) == {"items": []}
    assert commands == [
        [
            "kubectl",
            "--context",
            "fixture-context",
            "--namespace",
            "fixture",
            "--request-timeout=15s",
            "get",
            "deployments,statefulsets,replicasets,pods,persistentvolumeclaims",
            "-o",
            "json",
        ]
    ]
    with pytest.raises(boot.EvidenceError, match="explicit_target"):
        boot.collect_observations(
            replace(profile, target=replace(profile.target, cluster_context_ref=None)),
            runner,
        )
    assert len(commands) == 1


@pytest.mark.parametrize(
    "data", [b"not json SECRET", b"[]", b"x" * (boot.MAX_BYTES + 1)]
)
def test_bad_collector_payloads_are_redacted(profile, data):
    with pytest.raises(boot.EvidenceError) as error:
        boot.collect_observations(profile, lambda _: data)
    assert "SECRET" not in str(error.value)


def test_collector_timeout_fails_closed(profile):
    def runner(command):
        raise TimeoutError("SECRET")

    with pytest.raises(boot.EvidenceError, match="collection_unavailable"):
        boot.collect_observations(profile, runner)


def test_cli_fixture_path_never_collects(
    profile, snapshot, monkeypatch, tmp_path, capsys
):
    expected, observed = snapshot
    monkeypatch.setattr(boot, "load_environment_profile", lambda _: profile)

    def prohibited(*args):
        pytest.fail("fixture mode attempted cluster access")

    monkeypatch.setattr(boot, "collect_observations", prohibited)
    rendered, observations = tmp_path / "rendered.json", tmp_path / "observed.json"
    rendered.write_text(json.dumps(expected))
    observations.write_text(json.dumps(observed))
    assert (
        boot.main(
            [
                "--profile",
                "fixture",
                "--rendered",
                str(rendered),
                "--observations",
                str(observations),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["infrastructure_ready"] is True
    assert result["acceptance"] == "not_qualified"
    assert "fixture-context" not in json.dumps(result)


def test_foreign_application_evidence_stays_blocked(profile, snapshot):
    expected, observed = snapshot
    result = boot.verify_first_boot(
        profile,
        expected,
        observed,
        {
            "snapshot_digest": "different",
            "checks": {
                "engine": True,
                "identity": True,
                "administrator": True,
                "secrets": True,
            },
        },
    )
    assert result["application_evidence_status"] == "blocked"


@pytest.mark.parametrize(
    "field,value", [("readyReplicas", True), ("observedGeneration", "2")]
)
def test_malformed_numeric_status_never_counts_as_ready(
    profile, snapshot, field, value
):
    expected, observed = snapshot
    observed["items"][0]["status"][field] = value
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def test_declared_unused_pvc_is_not_silently_ignored(profile, snapshot):
    expected, observed = snapshot
    expected.append(resource("PersistentVolumeClaim", "missing"))
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


@pytest.mark.parametrize("kind", ["DaemonSet", "Job", "CronJob", "Pod"])
def test_unsupported_workload_is_not_silently_ignored(profile, snapshot, kind):
    expected, observed = snapshot
    expected.append(resource(kind, "unsupported"))
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def test_real_module_entrypoint_offline(profile, snapshot, tmp_path):
    import os
    import sys

    expected, observed = snapshot
    for item in expected + observed["items"]:
        item["metadata"]["namespace"] = "platform"
    rendered, observations = tmp_path / "rendered.json", tmp_path / "observed.json"
    rendered.write_text(json.dumps(expected))
    observations.write_text(json.dumps(observed))
    command = [
        sys.executable,
        "-m",
        "graph_os.deployment.kubernetes_first_boot",
        "--profile",
        "dev",
        "--rendered",
        str(rendered),
        "--observations",
        str(observations),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["infrastructure_ready"]
    assert report["application_evidence_status"] == "blocked"
    assert report["acceptance"] == "not_qualified"


@pytest.mark.parametrize(
    "constraint",
    [
        {"securityContext": {"runAsNonRoot": True}},
        {"serviceAccountName": "graphos"},
        {"automountServiceAccountToken": False},
        {"nodeSelector": {"workload": "graphos"}},
    ],
)
def test_pod_constraints_must_survive_controller_render(profile, snapshot, constraint):
    expected, observed = snapshot
    for item in (expected[0], observed["items"][0]):
        item["spec"]["template"]["spec"].update(copy.deepcopy(constraint))
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]
    observed["items"][2]["spec"].update(copy.deepcopy(constraint))
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]


@pytest.mark.parametrize(
    "failure",
    [
        "class",
        "request",
        "capacity",
        "terminating_claim",
        "terminating_workload",
        "claim_modes",
    ],
)
def test_pvc_contract_and_termination_are_required(profile, snapshot, failure):
    expected, observed = snapshot
    workload, _, _, claim = observed["items"]
    if failure == "class":
        claim["spec"]["storageClassName"] = "wrong"
    elif failure == "request":
        claim["spec"]["resources"]["requests"]["storage"] = "2Gi"
    elif failure == "capacity":
        claim["status"]["capacity"]["storage"] = "512Mi"
    elif failure == "terminating_claim":
        claim["metadata"]["deletionTimestamp"] = "now"
    elif failure == "terminating_workload":
        workload["metadata"]["deletionTimestamp"] = "now"
    elif failure == "claim_modes":
        claim["spec"]["accessModes"] = ["ReadOnlyMany"]
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def test_storage_quantities_allow_equivalent_api_spelling(profile, snapshot):
    expected, observed = snapshot
    observed["items"][3]["spec"]["resources"]["requests"]["storage"] = "1024Mi"
    observed["items"][3]["status"]["capacity"]["storage"] = "2Gi"
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]


@pytest.mark.parametrize(
    "failure",
    [
        "running_string",
        "running_timestamp",
        "owner_uid",
        "object_uid",
        "controller",
        "replicas",
        "ready",
        "condition",
        "capacity",
    ],
)
def test_malformed_identity_and_status_types_fail_closed(profile, snapshot, failure):
    expected, observed = snapshot
    workload, rs, pod, claim = observed["items"]
    status = pod["status"]["containerStatuses"][0]
    if failure == "running_string":
        status["state"]["running"] = "INVALID"
    elif failure == "running_timestamp":
        status["state"]["running"] = {"startedAt": []}
    elif failure == "owner_uid":
        rs["metadata"]["uid"] = ["forged"]
        pod["metadata"]["ownerReferences"][0]["uid"] = ["forged"]
    elif failure == "object_uid":
        pod["metadata"]["uid"] = []
    elif failure == "controller":
        pod["metadata"]["ownerReferences"][0]["controller"] = 1
    elif failure == "replicas":
        workload["spec"]["replicas"] = True
    elif failure == "ready":
        status["ready"] = 1
    elif failure == "condition":
        pod["status"]["conditions"][0]["status"] = True
    elif failure == "capacity":
        claim["status"]["capacity"]["storage"] = 1073741824
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


@pytest.mark.parametrize("failure", ["class", "capacity", "request"])
def test_stateful_generated_claim_must_match_template(profile, snapshot, failure):
    expected, observed, claim = stateful_snapshot(snapshot, "host-pod")
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]
    if failure == "class":
        claim["spec"]["storageClassName"] = "wrong"
    elif failure == "request":
        claim["spec"]["resources"]["requests"]["storage"] = "2Gi"
    else:
        claim["status"]["capacity"]["storage"] = "1Mi"
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


class FixtureStream:
    def __init__(self, chunks, *, stall=False):
        self.chunks = iter(chunks)
        self.stall = stall
        self.closed = False
        self.requested = []

    async def receive(self, max_bytes):
        self.requested.append(max_bytes)
        if self.stall:
            await anyio.sleep_forever()
        chunk = next(self.chunks, None)
        if chunk is None:
            raise anyio.EndOfStream
        assert len(chunk) <= max_bytes
        return chunk

    async def aclose(self):
        self.closed = True


class FixtureProcess:
    pid = 123456789

    def __init__(self, stream, *, code=0, stuck=False):
        self.stdout = stream
        self.returncode = None
        self.code = code
        self.stuck = stuck
        self.killed = False
        self.reaped = False

    def kill(self):
        self.killed = True

    async def wait(self):
        if self.stuck:
            await anyio.sleep_forever()
        self.returncode = self.code
        self.reaped = True
        return self.code


class FixtureNullFile:
    def __init__(self):
        self.wrapped = io.BytesIO()
        self.wrapped.name = boot.os.devnull

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.wrapped.close()


def inject_process(monkeypatch, process):
    monkeypatch.setattr(boot.shutil, "which", lambda name: "/fixture/kubectl")

    def kill_group(pid, sig):
        assert pid == process.pid
        assert sig == boot.signal.SIGKILL
        process.kill()

    monkeypatch.setattr(boot.os, "killpg", kill_group)

    async def null_file(path, mode):
        assert path == boot.os.devnull
        assert mode in {"rb", "wb"}
        return FixtureNullFile()

    monkeypatch.setattr(boot.anyio, "open_file", null_file)

    async def launch(command, *, stdin, stderr, start_new_session):
        assert start_new_session is True
        assert command[0] == "/fixture/kubectl"
        assert command[command.index("get") + 1] == boot._RESOURCE_TYPES
        assert stdin.read() == b""  # EOF, never inherited interactive input
        assert stderr.name == boot.os.devnull
        return process

    monkeypatch.setattr(boot.anyio, "open_process", launch)


@pytest.mark.parametrize(
    "mode", ["success", "oversize", "timeout", "nonzero", "cleanup_timeout"]
)
def test_streaming_collection_bounds_and_cleanup(profile, monkeypatch, mode):
    monkeypatch.setattr(boot, "MAX_BYTES", 16)
    monkeypatch.setattr(boot, "TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(boot, "CLEANUP_SECONDS", 0.02)
    stream = FixtureStream(
        [b'{"items": []}'] if mode != "oversize" else [b"x" * 16, b"x"],
        stall=mode in {"timeout", "cleanup_timeout"},
    )
    process = FixtureProcess(
        stream, code=1 if mode == "nonzero" else 0, stuck=mode == "cleanup_timeout"
    )
    inject_process(monkeypatch, process)
    if mode == "success":
        assert boot.collect_observations(profile) == {"items": []}
    else:
        code = {
            "oversize": "observation_too_large",
            "timeout": "collection_timeout",
            "nonzero": "collection_failed",
            "cleanup_timeout": "collection_cleanup_timeout",
        }[mode]
        with pytest.raises(boot.EvidenceError, match=code):
            boot.collect_observations(profile)
    assert stream.closed
    assert process.reaped is (mode != "cleanup_timeout")
    if mode in {"oversize", "timeout", "cleanup_timeout"}:
        assert process.killed
    if mode == "oversize":
        assert stream.requested == [17, 1]


def test_cancellation_kills_and_reaps_without_swallowing_cancel(monkeypatch):
    stream = FixtureStream([], stall=True)
    process = FixtureProcess(stream)
    inject_process(monkeypatch, process)

    async def cancelled():
        with anyio.move_on_after(0.02) as scope:
            await boot._collect_process(["kubectl", "get", boot._RESOURCE_TYPES])
        assert scope.cancel_called

    anyio.run(cancelled)
    assert process.killed and process.reaped and stream.closed


@pytest.mark.parametrize(
    "context,namespace",
    [
        ("--other", "fixture"),
        ("x\nother", "fixture"),
        ([], "fixture"),
        ("fixture", "--all-namespaces"),
        ("fixture", True),
    ],
)
def test_invalid_target_never_reaches_runner(profile, context, namespace):
    invalid = replace(
        profile,
        target=replace(
            profile.target, cluster_context_ref=context, namespace=namespace
        ),
    )

    def forbidden(_):
        pytest.fail("invalid target reached runner")

    with pytest.raises(boot.EvidenceError, match="explicit_target_required"):
        boot.collect_observations(invalid, forbidden)


def test_explicit_api_additions_preserve_rendered_constraints(profile, snapshot):
    expected, observed = snapshot
    pod = observed["items"][2]
    for spec in (
        expected[0]["spec"]["template"]["spec"],
        observed["items"][0]["spec"]["template"]["spec"],
        pod["spec"],
    ):
        spec["securityContext"] = {"runAsNonRoot": True}
        spec["serviceAccountName"] = "graphos"
    pod["spec"].update(
        {
            "nodeName": "fixture-node",
            "dnsPolicy": "ClusterFirst",
            "restartPolicy": "Always",
        }
    )
    inject_service_account(pod)
    assert boot.verify_first_boot(profile, expected, observed)["infrastructure_ready"]
    pod["spec"]["securityContext"]["runAsNonRoot"] = False
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def test_collection_accepts_exact_byte_cap(profile, monkeypatch):
    payload = b'{"items": []}'
    monkeypatch.setattr(boot, "MAX_BYTES", len(payload))
    process = FixtureProcess(FixtureStream([payload]))
    inject_process(monkeypatch, process)
    assert boot.collect_observations(profile) == {"items": []}
    assert process.stdout.requested == [len(payload) + 1, 1]
    assert process.reaped and process.stdout.closed


def test_missing_trusted_executable_refuses_before_launch(profile, monkeypatch):
    monkeypatch.setattr(boot.shutil, "which", lambda _: None)

    async def prohibited(*args, **kwargs):
        pytest.fail("missing executable reached launch")

    monkeypatch.setattr(boot.anyio, "open_process", prohibited)
    with pytest.raises(boot.EvidenceError, match="collection_unavailable"):
        boot.collect_observations(profile)


def test_read_failure_is_redacted_and_cleans_up(profile, monkeypatch):
    class BrokenStream(FixtureStream):
        async def receive(self, max_bytes):
            raise OSError("SECRET")

    process = FixtureProcess(BrokenStream([]))
    inject_process(monkeypatch, process)
    with pytest.raises(boot.EvidenceError, match="collection_unavailable") as error:
        boot.collect_observations(profile)
    assert "SECRET" not in str(error.value)
    assert process.killed and process.reaped and process.stdout.closed


def inject_service_account(pod, field="containers"):
    name = "kube-api-access-abcde"
    volume = {
        "name": name,
        "projected": {
            "defaultMode": 420,
            "sources": [
                {"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
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
    mount = {
        "name": name,
        "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
        "readOnly": True,
    }
    pod["spec"]["volumes"].append(volume)
    pod["spec"][field][0].setdefault("volumeMounts", []).append(mount)
    return volume, mount


@pytest.mark.parametrize("field", ["containers", "initContainers"])
def test_api_mount_is_allowed_for_regular_and_native_sidecar_containers(
    profile, snapshot, field
):
    expected, observed = snapshot
    pod = observed["items"][2]
    if field == "initContainers":
        sidecar = {
            "name": "engine",
            "image": IMAGE,
            "restartPolicy": "Always",
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        }
        for spec in (
            expected[0]["spec"]["template"]["spec"],
            observed["items"][0]["spec"]["template"]["spec"],
            pod["spec"],
        ):
            spec[field] = [copy.deepcopy(sidecar)]
        pod["status"]["initContainerStatuses"] = [
            {
                "name": "engine",
                "imageID": IMAGE,
                "ready": True,
                "state": {"running": {}},
            }
        ]
    inject_service_account(pod, field)
    pod["spec"][field][0]["volumeMounts"].reverse()
    result = boot.verify_first_boot(profile, expected, observed)
    assert result["infrastructure_ready"]
    assert result["acceptance"] == "not_qualified"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_rendered",
        "changed_rendered_path",
        "changed_rendered_subpath",
        "extra_mount",
        "unlinked",
        "writable",
        "wrong_path",
        "foreign_projection",
        "extra_projection",
        "invalid_token_expiry",
        "wrong_mode",
        "automount_disabled",
    ],
)
def test_injected_mount_never_masks_drift_or_unvalidated_additions(
    profile, snapshot, failure
):
    expected, observed = snapshot
    pod = observed["items"][2]
    volume, mount = inject_service_account(pod)
    mounts = pod["spec"]["containers"][0]["volumeMounts"]
    _damage_rendered_mount(expected, observed, pod, volume, mount, mounts, failure)
    _damage_api_projection(expected, observed, pod, volume, mount, failure)
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]


def _damage_rendered_mount(expected, observed, pod, volume, mount, mounts, failure):
    if failure == "missing_rendered":
        mounts.pop(0)
    elif failure == "changed_rendered_path":
        mounts[0]["mountPath"] = "/wrong"
    elif failure == "changed_rendered_subpath":
        for spec in (
            expected[0]["spec"]["template"]["spec"],
            observed["items"][0]["spec"]["template"]["spec"],
        ):
            spec["containers"][0]["volumeMounts"][0]["subPath"] = "rendered"
        mounts[0]["subPath"] = "wrong"
    elif failure == "extra_mount":
        mounts.append({"name": "data", "mountPath": "/extra"})
    elif failure == "unlinked":
        pod["spec"]["volumes"].remove(volume)
    elif failure == "writable":
        mount["readOnly"] = False


def _damage_api_projection(expected, observed, pod, volume, mount, failure):
    if failure == "wrong_path":
        mount["mountPath"] = "/token"
    elif failure == "foreign_projection":
        volume["projected"]["sources"][1]["configMap"]["name"] = "other-ca"
    elif failure == "extra_projection":
        volume["projected"]["sources"].append({"secret": {"name": "foreign"}})
    elif failure == "invalid_token_expiry":
        volume["projected"]["sources"][0]["serviceAccountToken"][
            "expirationSeconds"
        ] = True
    elif failure == "wrong_mode":
        volume["projected"]["defaultMode"] = 511
    elif failure == "automount_disabled":
        for spec in (
            expected[0]["spec"]["template"]["spec"],
            observed["items"][0]["spec"]["template"]["spec"],
            pod["spec"],
        ):
            spec["automountServiceAccountToken"] = False


@pytest.mark.parametrize("known_exited", [True, False])
def test_missing_cleanup_group_requires_known_exit(monkeypatch, known_exited):
    process = FixtureProcess(FixtureStream([]))
    process.returncode = 0 if known_exited else None

    def gone(pid, sig):
        raise ProcessLookupError("already gone")

    monkeypatch.setattr(boot.os, "killpg", gone)
    if known_exited:
        anyio.run(boot._close_process, process)
        assert process.reaped and process.stdout.closed
    else:
        with pytest.raises(boot.EvidenceError, match="collection_cleanup_uncertain"):
            anyio.run(boot._close_process, process)
        assert process.reaped and process.stdout.closed


@pytest.mark.parametrize(
    "path,value",
    [
        ((0, "serviceAccountToken", "path"), "other-token"),
        ((0, "serviceAccountToken", "audience"), "foreign"),
        ((1, "configMap", "items", 0, "path"), "other-ca"),
        ((2, "downwardAPI", "items", 0, "path"), "other-namespace"),
        ((2, "downwardAPI", "items", 0, "fieldRef", "fieldPath"), "spec.nodeName"),
        ((2, "downwardAPI", "items", 0, "fieldRef", "extra"), "foreign"),
    ],
)
def test_api_projection_fields_remain_exact(profile, snapshot, path, value):
    expected, observed = snapshot
    volume, _ = inject_service_account(observed["items"][2])
    target = volume["projected"]["sources"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not boot.verify_first_boot(profile, expected, observed)[
        "infrastructure_ready"
    ]
