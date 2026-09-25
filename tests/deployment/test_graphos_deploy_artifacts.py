"""Static contract of the shipped Helm chart and single-host Compose project."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "graph-os"
COMPOSE = ROOT / "deploy" / "compose"


def _templates() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CHART / "templates").rglob("*")
        if path.is_file()
    )


def test_chart_is_namespace_safe_and_schema_backed() -> None:
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]

    assert props["topology"]["enum"] == ["unified-in-process", "out-of-process-shared"]
    assert props["identity"]["properties"]["mode"]["enum"] == [
        "none",
        "local",
        "external",
    ]
    assert props["secrets"]["properties"]["backend"]["enum"] == ["engine", "vault"]
    assert schema["$defs"]["engine"]["properties"]["placement"]["enum"] == [
        "sidecar",
        "child",
    ]
    rendered_source = _templates()
    for kind in ("Namespace", "ClusterRole", "ClusterRoleBinding", "Secret"):
        assert f"kind: {kind}\n" not in rendered_source


def test_chart_defaults_fail_safe() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))

    assert values["graphos"]["image"]["repository"].startswith("registry.invalid/")
    assert values["runtimeSecret"]["optional"] is False
    assert values["topology"] == "unified-in-process"
    assert values["engine"]["placement"] == "sidecar"
    assert values["identity"]["mode"] == "local"
    assert values["identity"]["noneExposeAck"] == ""
    assert values["enableServiceLinks"] is False


def test_chart_guards_none_mode_and_engine_tls() -> None:
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")

    assert "I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN" in helpers
    assert "engine.tls.secretName is required" in helpers
    assert "restartPolicy: Always" in (CHART / "templates" / "graphos.yaml").read_text()


def test_compose_is_single_writer_loopback_and_secret_free() -> None:
    compose = yaml.safe_load((COMPOSE / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert list(services) == ["graph-os"]
    service = services["graph-os"]
    assert "deploy" not in service
    assert service["image"].startswith("${GRAPHOS_IMAGE:?")
    assert all(port.startswith("127.0.0.1:") for port in service["ports"])
    assert service["environment"]["GRAPH_SERVICE_PERSIST_DIR"].startswith("/var/lib/")
    environment = json.dumps(service["environment"])
    assert not re.search(r"(SECRET|TOKEN|PASSWORD)\"\s*:\s*\"[^$\"]", environment)
    assert (COMPOSE / ".gitignore").read_text(encoding="utf-8").split() == [
        ".env",
        "graph-os.env",
    ]


def test_skill_provider_is_registered_and_packaged() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '[project.entry-points."agent_utilities.skill_providers"]' in pyproject
    assert 'graph-os = "graph_os.skills"' in pyproject
    assert '"skills/**/*.md"' in pyproject
