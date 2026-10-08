"""Rendered-image policy fixtures; no Helm, registry, engine or cluster required."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/check_deployment_image_digests.py"
)
SPEC = importlib.util.spec_from_file_location("check_deployment_image_digests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

DIGEST = "0123456789abcdef" * 4
PINNED = f"registry.example/team/worker@sha256:{DIGEST}"
KINDS = (
    "Pod",
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
    "Job",
    "CronJob",
)


def _workload(kind: str = "Pod", image: object = PINNED) -> dict[str, Any]:
    spec: dict[str, Any] = {"containers": [{"name": "worker", "image": image}]}
    if kind != "Pod":
        spec = {"template": {"spec": spec}}
    if kind == "CronJob":
        spec = {"jobTemplate": {"spec": spec}}
    return {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": {"name": "worker"},
        "spec": spec,
    }


def _write(tmp_path: Path, *documents: object, name: str = "rendered.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump_all(documents), encoding="utf-8")
    return path


def _check(tmp_path: Path, *documents: object) -> tuple[int, list[str]]:
    return checker.check_file(_write(tmp_path, *documents))


@pytest.mark.parametrize("kind", KINDS)
def test_all_agreed_workloads_accept_pins(tmp_path: Path, kind: str) -> None:
    assert _check(tmp_path, _workload(kind)) == (1, [])


@pytest.mark.parametrize("kind", KINDS)
def test_all_agreed_workloads_reject_floating_images(tmp_path: Path, kind: str) -> None:
    count, errors = _check(tmp_path, _workload(kind, "worker:latest"))
    assert count == 0
    assert len(errors) == 1
    assert f"{kind}/worker" in errors[0]
    assert "containers[0].image" in errors[0]


@pytest.mark.parametrize(
    "repository",
    [
        "worker",
        "team/worker",
        "registry.example:5000/team/worker",
        "localhost:5000/worker",
        "registry.example/team/image.part__two--three",
        "a" * 255,
    ],
)
def test_supported_repository_forms(tmp_path: Path, repository: str) -> None:
    assert _check(tmp_path, _workload(image=f"{repository}@sha256:{DIGEST}")) == (1, [])


@pytest.mark.parametrize(
    "image",
    [
        None,
        True,
        123,
        [],
        {},
        "",
        "worker",
        "worker:v1",
        "worker:latest",
        f"worker:v1@sha256:{DIGEST}",
        f"@sha256:{DIGEST}",
        f"worker@sha512:{DIGEST}",
        f"worker@SHA256:{DIGEST}",
        f"worker@sha256:{DIGEST.upper()}",
        f"worker@sha256:{DIGEST[:-1]}",
        f"worker@sha256:{DIGEST}0",
        f"worker@sha256:{'g' * 64}",
        f" worker@sha256:{DIGEST}",
        f"worker@sha256:{DIGEST}\n",
        f"worker@sha256:{DIGEST}@sha256:{DIGEST}",
        f"https://registry.example/worker@sha256:{DIGEST}",
        f"registry.example//worker@sha256:{DIGEST}",
        f"/worker@sha256:{DIGEST}",
        f"worker/@sha256:{DIGEST}",
        f"registry.example:/worker@sha256:{DIGEST}",
        f"registry.example:port/worker@sha256:{DIGEST}",
        f"registry..example/worker@sha256:{DIGEST}",
        f"Worker@sha256:{DIGEST}",
        f"reg_istry.example/worker@sha256:{DIGEST}",
        f"registry.-example/worker@sha256:{DIGEST}",
        f"[2001:db8::1]:5000/worker@sha256:{DIGEST}",
        f"{'a' * 256}@sha256:{DIGEST}",
        f"$REPOSITORY@sha256:{DIGEST}",
        "{{ .Values.image }}",
    ],
)
def test_malformed_and_unsupported_references_fail(
    tmp_path: Path, image: object
) -> None:
    _, errors = _check(tmp_path, _workload(image=image))
    assert len(errors) == 1
    assert "containers[0].image: expected repository@sha256:" in errors[0]


def test_containers_and_native_sidecar_are_checked(tmp_path: Path) -> None:
    workload = _workload("Deployment")
    pod = workload["spec"]["template"]["spec"]
    pod["containers"].append({"name": "proxy", "image": PINNED})
    pod["initContainers"] = [
        {"name": "engine", "image": PINNED, "restartPolicy": "Always"}
    ]
    assert _check(tmp_path, workload) == (3, [])
    pod["initContainers"][0]["image"] = "engine:latest"
    _, errors = _check(tmp_path, workload)
    assert "spec.template.spec.initContainers[0].image" in errors[0]
    pod["initContainers"][0]["image"] = PINNED
    pod["containers"][1]["image"] = "proxy:latest"
    _, errors = _check(tmp_path, workload)
    assert "containers[1].image" in errors[0]


@pytest.mark.parametrize("field", ["containers", "initContainers"])
@pytest.mark.parametrize("value", [None, {}, "wrong", [None], ["wrong"], [{}]])
def test_malformed_container_lists_fail(
    tmp_path: Path, field: str, value: object
) -> None:
    workload = _workload()
    workload["spec"][field] = value
    _, errors = _check(tmp_path, workload)
    assert f".spec.{field}" in errors[0]


@pytest.mark.parametrize(
    "spec", [{}, {"containers": []}, {"initContainers": [{"image": PINNED}]}]
)
def test_regular_containers_must_be_nonempty(tmp_path: Path, spec: object) -> None:
    workload = _workload()
    workload["spec"] = spec
    _, errors = _check(tmp_path, workload)
    assert "containers: expected a nonempty list" in errors[0]


@pytest.mark.parametrize("kind", KINDS)
def test_missing_pod_spec_is_not_ignored(tmp_path: Path, kind: str) -> None:
    workload = _workload(kind)
    workload["spec"] = {}
    _, errors = _check(tmp_path, workload)
    assert f"{kind}/worker" in errors[0]


def test_cronjob_requires_the_complete_template_path(tmp_path: Path) -> None:
    workload = _workload("CronJob")
    workload["spec"]["jobTemplate"]["spec"]["template"] = None
    _, errors = _check(tmp_path, workload)
    assert "spec.jobTemplate.spec.template: expected a mapping" in errors[0]


def test_recursive_lists_mixed_documents_and_nonworkloads(tmp_path: Path) -> None:
    nested: dict[str, Any] = {
        "kind": "List",
        "items": [_workload("CronJob"), _workload("DaemonSet")],
    }
    resources = {"kind": "List", "items": [nested, {"kind": "Service"}]}
    assert _check(
        tmp_path, None, resources, {"kind": "HorizontalPodAutoscaler"}, _workload()
    ) == (3, [])
    nested["items"][1]["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "floating"
    )
    _, errors = _check(tmp_path, resources)
    assert "items[0] (List/<unnamed>).items[1] (DaemonSet/worker)" in errors[0]


@pytest.mark.parametrize(
    "resource",
    [
        [],
        "text",
        42,
        {},
        {"kind": []},
        {"kind": ""},
        {"kind": "List"},
        {"kind": "List", "items": {}},
        {"kind": "List", "items": [None]},
    ],
)
def test_malformed_documents_and_lists_fail(tmp_path: Path, resource: object) -> None:
    _, errors = _check(tmp_path, resource)
    assert len(errors) == 1
    assert "rendered.yaml: document 1" in errors[0]


@pytest.mark.parametrize(
    "text",
    [
        "kind: Pod\nkind: Service\n",
        "kind: Pod\nspec:\n  containers: []\n  containers: []\n",
        f"kind: Pod\nspec:\n  containers:\n  - image: floating\n    image: {PINNED}\n",
        f"kind: Pod\nspec:\n  containers:\n  - image: {PINNED}\n    image: floating\n",
        "kind: ConfigMap\ndata: {key: first, key: second}\n",
    ],
)
def test_duplicate_keys_are_never_silently_discarded(tmp_path: Path, text: str) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(text, encoding="utf-8")
    _, errors = checker.check_file(path)
    assert "duplicate key" in errors[0]
    assert "line " in errors[0]


@pytest.mark.parametrize(
    "text, message",
    [
        ("kind: Pod\nspec: {<<: {containers: []}}", "merge keys are unsupported"),
        ("kind: ConfigMap\ndata: {true: value}", "only string keys"),
        ("kind: ConfigMap\ndata: {[a, b]: value}", "only string keys"),
        ("kind: Pod\nspec: [", "expected the node content"),
        (
            "!!python/object/apply:os.system ['echo unsafe']",
            "could not determine a constructor",
        ),
        ("&loop {kind: List, items: [*loop]}", "cyclic List aliases"),
    ],
)
def test_unsupported_yaml_forms_fail(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "unsupported.yaml"
    path.write_text(text, encoding="utf-8")
    _, errors = checker.check_file(path)
    assert message in errors[0]


def test_noncyclic_aliases_check_each_occurrence(tmp_path: Path) -> None:
    workload = _workload()
    assert _check(tmp_path, {"kind": "List", "items": [workload, workload]}) == (2, [])


def test_excessive_yaml_nesting_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "deep.yaml"
    path.write_text("{kind: List, items: [" * 1200 + "]}" * 1200, encoding="utf-8")
    assert checker.main([str(path)]) == 1
    output = capsys.readouterr()
    assert "deep.yaml" in output.err
    assert "recursion" in output.err
    assert "Traceback" not in output.err


def test_out_of_scope_forms_do_not_count_as_workloads(tmp_path: Path) -> None:
    # No heuristic discovery in CRDs, typed Lists, config data or ephemeral containers.
    typed_list = {"kind": "PodList", "items": [_workload(image="floating")]}
    custom = _workload("CustomWorker", "floating")
    pod = _workload()
    pod["spec"]["ephemeralContainers"] = [{"image": "floating"}]
    assert _check(
        tmp_path, typed_list, custom, {"kind": "ConfigMap", "image": "floating"}, pod
    ) == (1, [])


@pytest.mark.parametrize(
    "documents",
    [
        (),
        (None,),
        ({"kind": "Service"},),
        ({"kind": "List", "items": []},),
        ({"kind": "PodList", "items": [_workload()]},),
    ],
)
def test_cli_rejects_zero_container_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], documents: tuple
) -> None:
    assert checker.main([str(_write(tmp_path, *documents))]) == 1
    assert "no containers checked" in capsys.readouterr().err


def test_cli_checks_every_explicit_file_and_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    valid = _write(tmp_path, _workload(), name="valid.yaml")
    mixed = _write(
        tmp_path, _workload("Job"), _workload("Pod", "floating"), name="mixed.yaml"
    )
    service = _write(tmp_path, {"kind": "Service"}, name="service.yaml")
    assert checker.main([str(valid), str(service)]) == 0
    assert "Checked 1 container images" in capsys.readouterr().out
    assert checker.main([str(valid), str(mixed)]) == 1
    output = capsys.readouterr()
    assert "mixed.yaml: document 2 (Pod/worker).spec.containers[0].image" in output.err
    assert not output.out


@pytest.mark.parametrize(
    "input_kind", ["missing", "directory", "encoding", "late-parse-error"]
)
def test_input_failures_are_actionable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], input_kind: str
) -> None:
    path = tmp_path / "input.yaml"
    if input_kind == "directory":
        path.mkdir()
    elif input_kind == "encoding":
        path.write_bytes(b"\xff\xfe")
    elif input_kind == "late-parse-error":
        path.write_text(yaml.safe_dump(_workload()) + "---\nkind: [", encoding="utf-8")
    assert checker.main([str(path)]) == 1
    output = capsys.readouterr()
    assert str(path) in output.err
    assert "Traceback" not in output.err
    assert not output.out


def test_real_cli_has_explicit_inputs_and_exit_status(tmp_path: Path) -> None:
    path = _write(tmp_path, _workload())
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Checked 1 container images" in result.stdout
    assert not result.stderr
    path.write_text("kind: Pod\nspec: {}", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "containers" in result.stderr
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2  # argparse usage error: no implicit search or stdin.
    assert "paths" in result.stderr
