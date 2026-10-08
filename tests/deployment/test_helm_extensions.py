"""Render extension workloads and their namespace-scoped network contract."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "graph-os"
NAMESPACE = "extension-fixture"
LOCAL_PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}}
}
INGRESS_PEER = {
    "namespaceSelector": {
        "matchLabels": {"kubernetes.io/metadata.name": "ingress-fixture"}
    }
}


def _extension(name: str, port: int) -> dict[str, Any]:
    return {
        "name": name,
        "port": port,
        "image": {
            "repository": "registry.invalid/fixture/connector",
            "digest": "sha256:" + "a" * 64,
        },
    }


def _render(tmp_path: Path, values: dict[str, Any]) -> list[dict[str, Any]]:
    helm = shutil.which("helm")
    assert helm is not None, "Helm must be provisioned to run chart rendering tests"
    value_file = tmp_path / "values.yaml"
    value_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    env = dict(os.environ, KUBECONFIG=os.devnull)
    for name in (
        "HELM_CACHE_HOME",
        "HELM_CONFIG_HOME",
        "HELM_DATA_HOME",
        "HELM_PLUGINS",
    ):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[name] = str(directory)
    flags = ["--kube-version", "1.31.0", "--values", str(value_file)]
    commands = [
        [helm, "lint", str(CHART), "--strict", *flags],
        [helm, "template", "fixture", str(CHART), "--namespace", NAMESPACE, *flags],
    ]
    outputs = []
    for command in commands:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False, env=env
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(result.stdout)
    return [document for document in yaml.safe_load_all(outputs[-1]) if document]


def _ports_for_peer(policy: dict[str, Any], peer: dict[str, Any]) -> set[int]:
    ports: set[int] = set()
    for rule in policy["spec"]["ingress"]:
        if peer in rule["from"]:
            assert rule["ports"], "An ingress rule must not allow every port"
            assert all(port["protocol"] == "TCP" for port in rule["ports"])
            ports.update(port["port"] for port in rule["ports"])
    return ports


def _values(
    mode: str, pull_secrets: list[dict[str, str]], extensions: str
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "imagePullSecrets": pull_secrets,
        "graphos": {"image": {"digest": "sha256:" + "b" * 64}},
        "engine": {"placement": "child" if mode == "child" else "sidecar"},
        "networkPolicy": {"ingressFrom": [INGRESS_PEER]},
        "connectors": [],
        "components": [],
    }
    if mode == "shared":
        values["topology"] = "out-of-process-shared"
        values["engine"]["tls"] = {"secretName": "fixture-engine-tls"}
    if extensions in {"connector", "both", "shared-port"}:
        values["connectors"] = [_extension("sample-mcp", 9123)]
    if extensions in {"component", "both", "shared-port"}:
        port = 9123 if extensions == "shared-port" else 9234
        values["components"] = [_extension("sample-component", port)]
    return values


def _assert_extension_pods(
    documents: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    pull_secrets: list[dict[str, str]],
) -> None:
    deployments = {
        document["metadata"]["labels"]["graph-os.extension/name"]: document
        for document in documents
        if document["kind"] == "Deployment"
        and "graph-os.extension/name" in document["metadata"]["labels"]
    }
    assert set(deployments) == {extension["name"] for extension in expected}
    for extension in expected:
        pod = deployments[extension["name"]]["spec"]["template"]["spec"]
        assert pod.get("imagePullSecrets", []) == pull_secrets
        assert pod["automountServiceAccountToken"] is False
        assert pod["containers"][0]["ports"][0]["containerPort"] == extension["port"]


def _assert_network_policy(
    documents: list[dict[str, Any]], expected: list[dict[str, Any]], mode: str
) -> None:
    policy = next(
        document for document in documents if document["kind"] == "NetworkPolicy"
    )
    core_ports = {8000, 8080} | ({9100} if mode == "shared" else set())
    extension_ports = {extension["port"] for extension in expected}
    assert _ports_for_peer(policy, LOCAL_PEER) == core_ports | extension_ports
    assert _ports_for_peer(policy, INGRESS_PEER) == core_ports
    rules = policy["spec"]["ingress"]
    assert len(rules) == (2 if expected else 1)
    if expected:
        assert rules[1]["from"] == [LOCAL_PEER]
        assert len(rules[1]["ports"]) == len(extension_ports)
    _assert_default_egress(policy)


def _assert_default_egress(policy: dict[str, Any]) -> None:
    assert policy["spec"]["egress"] == [
        {"to": [LOCAL_PEER]},
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    }
                }
            ],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
    ]


@pytest.mark.parametrize("mode", ["sidecar", "child", "shared"])
@pytest.mark.parametrize("pull_secrets", [[], [{"name": "fixture-registry-pull"}]])
@pytest.mark.parametrize(
    "extensions", ["none", "connector", "component", "both", "shared-port"]
)
def test_extension_render_contract(
    tmp_path: Path, mode: str, pull_secrets: list[dict[str, str]], extensions: str
) -> None:
    values = _values(mode, pull_secrets, extensions)
    documents = _render(tmp_path, values)
    expected = values["connectors"] + values["components"]
    _assert_extension_pods(documents, expected, pull_secrets)
    _assert_network_policy(documents, expected, mode)


def test_disabled_network_policy_renders_no_policy(tmp_path: Path) -> None:
    documents = _render(
        tmp_path,
        {
            "networkPolicy": {"enabled": False},
            "connectors": [_extension("sample-mcp", 9123)],
        },
    )
    assert all(document["kind"] != "NetworkPolicy" for document in documents)
