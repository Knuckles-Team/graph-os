"""Tests for the host dependency preflight."""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from types import SimpleNamespace

import pytest
import yaml

from graph_os.deployment import genesis_environments as environments
from graph_os.deployment import preflight as P


@pytest.fixture(autouse=True)
def no_engine_process(monkeypatch):
    monkeypatch.setattr(P, "_engine_binary_path", lambda: None)


@pytest.fixture
def core_ready(monkeypatch):
    for function, name in (
        ("_check_python", "python"),
        ("_check_installer", "installer"),
        ("_check_engine", "engine_binary"),
    ):
        monkeypatch.setattr(
            P, function, lambda name=name: P._result(name, "ok", "fixture")
        )


def test_report_shape_and_python_check():
    rep = P.run_preflight("tiny")
    assert set(rep) >= {
        "status",
        "profile",
        "components",
        "counts",
        "checks",
        "summary",
    }
    assert rep["profile"] == "tiny"
    names = [c["name"] for c in rep["checks"]]
    # tiny always checks python/installer/engine; docker is a skip (not required).
    assert {"python", "installer", "engine_binary", "docker"} <= set(names)
    assert rep["status"] in ("ready", "warnings", "blocked")


def test_docker_required_for_single_node_profile(monkeypatch):
    monkeypatch.setattr(P.shutil, "which", lambda name: None)  # nothing on PATH
    tiny = {c["name"]: c for c in P.run_preflight("tiny")["checks"]}
    single = {c["name"]: c for c in P.run_preflight("single-node-prod")["checks"]}
    assert tiny["docker"]["status"] == "skip"
    assert single["docker"]["status"] == "fail"


def test_engine_is_wheel_first_rust_only_fallback(monkeypatch):
    # Engine binary absent → warn, and the remediation must point at the wheel, not Rust.
    monkeypatch.setattr(P, "_engine_binary_path", lambda: None)
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    res = P._check_engine()
    assert res["status"] == "warn"
    assert "pip install agent-utilities" in res["remediation"]
    assert "only needed if no prebuilt wheel" in res["remediation"].lower()


def test_engine_present_means_no_rust(monkeypatch):
    monkeypatch.setattr(
        P, "_engine_binary_path", lambda: "/venv/bin/epistemic-graph-server"
    )
    # _check_engine also verifies the binary satisfies the current launch
    # contract (--idle-shutdown-secs) via a real subprocess call — legitimate
    # protection against a stale/partial engine artifact silently reporting
    # "ok". The fake path above isn't executable, so also satisfy that check
    # explicitly rather than weakening it, to exercise the real "binary
    # present AND contract verified -> no Rust needed" behavior.
    monkeypatch.setattr(P, "_engine_binary_contract", lambda server_path: "current")
    res = P._check_engine()
    assert res["status"] == "ok" and "no Rust needed" in res["detail"]


def test_geniusbot_blocks_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    rep = P.run_preflight("tiny", ["geniusbot"])
    gb = next(c for c in rep["checks"] if c["name"] == "geniusbot")
    assert gb["status"] == "fail"
    assert rep["status"] == "blocked"


def test_unknown_component_is_skip_not_crash():
    rep = P.run_preflight("tiny", ["nope"])
    c = next(c for c in rep["checks"] if c["name"] == "nope")
    assert c["status"] == "skip"


def test_component_checks_never_raise():
    for name, fn in P._COMPONENT_CHECKS.items():
        res = fn()
        assert set(res) >= {"name", "status", "detail"}
        assert res["status"] in ("ok", "warn", "fail", "skip", "error")


@pytest.mark.parametrize("profile", ["dev", "test", "prod", "enterprise"])
@pytest.mark.parametrize("missing", ["kubectl", "helm", "both"])
def test_kubernetes_missing_tools_block_despite_docker(
    profile, missing, core_ready, monkeypatch
):
    def which(name):
        return (
            None
            if name == missing or missing == "both" and name != "docker"
            else f"/tools/{name}"
        )

    monkeypatch.setattr(P.shutil, "which", which)
    monkeypatch.setattr(
        P.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    report = P.run_preflight(profile)
    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "blocked"
    assert "docker" not in checks
    for tool in ("kubectl", "helm"):
        assert checks[tool]["status"] == ("fail" if missing in (tool, "both") else "ok")


@pytest.mark.parametrize("profile", ["dev", "test", "prod", "enterprise"])
def test_kubernetes_probes_are_client_only_bounded_and_not_acceptance(
    profile, core_ready, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        P.shutil,
        "which",
        lambda name: f"/tools/{name}" if name in {"kubectl", "helm"} else None,
    )

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(P.subprocess, "run", run)
    report = P.run_preflight(profile)
    assert report["status"] == "ready"
    assert [call[0] for call in calls] == [
        ["/tools/kubectl", "version", "--client=true"],
        ["/tools/helm", "version", "--short"],
    ]
    assert all(
        kwargs
        == {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 5,
            "check": False,
        }
        for _, kwargs in calls
    )
    for check in report["checks"][-2:]:
        assert "cluster and first boot remain unverified" in check["detail"]


@pytest.mark.parametrize("tool", ["kubectl", "helm"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_kubernetes_probe_failures_are_blocking_and_private(
    tool, failure, core_ready, monkeypatch
):
    monkeypatch.setattr(P.shutil, "which", lambda name: f"/private/{name}")

    def run(argv, **kwargs):
        if argv[0].endswith(tool):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 5, output="private-output")
            if failure == "oserror":
                raise OSError("private-output")
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(P.subprocess, "run", run)
    report = P.run_preflight("prod")
    assert report["status"] == "blocked"
    assert (
        next(check for check in report["checks"] if check["name"] == tool)["status"]
        == "fail"
    )
    assert "private-output" not in json.dumps(report)
    assert "/private/" not in json.dumps(report)


def test_named_extension_uses_validated_target_not_its_name(
    tmp_path, core_ready, monkeypatch
):
    raw = yaml.safe_load(
        (environments.BUILTIN_ENVIRONMENTS_DIR / "dev.yaml").read_text()
    )
    raw["environment"]["name"] = "custom"
    (tmp_path / "custom.yaml").write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(environments, "_extension_dir", lambda: tmp_path)
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    report = P.run_preflight("custom")
    assert report["status"] == "blocked"
    assert {check["name"] for check in report["checks"]} >= {"kubectl", "helm"}


@pytest.mark.parametrize("failure", ["unknown", "malformed"])
def test_invalid_profiles_do_not_fall_back(failure, tmp_path, core_ready, monkeypatch):
    if failure == "malformed":
        (tmp_path / "invalid.yaml").write_text("private_value: secret-fixture\n")
    monkeypatch.setattr(environments, "_extension_dir", lambda: tmp_path)
    monkeypatch.setattr(
        P.shutil,
        "which",
        lambda name: pytest.fail("invalid profile must not probe tools"),
    )
    report = P.run_preflight("invalid")
    assert report["status"] == "blocked"
    assert report["checks"][-1]["name"] == "deployment_target"
    assert "secret-fixture" not in json.dumps(report)
    assert str(tmp_path) not in json.dumps(report)


def test_doctor_cli_propagates_kubernetes_prerequisite_refusal(
    core_ready, monkeypatch, capsys
):
    from graph_os.deployment.doctor import _run_preflight_cli

    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    result = _run_preflight_cli(Namespace(profile="prod", components=None, json=True))
    report = json.loads(capsys.readouterr().out)
    assert result == 1
    assert report["profile"] == "prod"
    assert report["status"] == "blocked"
    assert {
        check["name"] for check in report["checks"] if check["status"] == "fail"
    } == {"kubectl", "helm"}


@pytest.mark.parametrize(
    "target,expected",
    [
        ("bare-metal", "ready"),
        ("docker-compose", "blocked"),
        ("docker-swarm", "blocked"),
        ("podman", "blocked"),
    ],
)
def test_named_non_kubernetes_target_never_runs_chart_probes(
    target, expected, tmp_path, core_ready, monkeypatch
):
    raw = yaml.safe_load(
        (environments.BUILTIN_ENVIRONMENTS_DIR / "dev.yaml").read_text()
    )
    raw["environment"]["name"] = "custom"
    raw["target"]["orchestrator"] = target
    (tmp_path / "custom.yaml").write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(environments, "_extension_dir", lambda: tmp_path)
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        P.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("no chart probe for this target"),
    )
    report = P.run_preflight("custom")
    assert report["status"] == expected
    assert not {"kubectl", "helm"} & {check["name"] for check in report["checks"]}
    if target == "podman":
        assert report["checks"][-1]["name"] == "deployment_target"
