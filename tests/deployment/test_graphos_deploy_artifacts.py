"""Static contract of the shipped Helm chart and single-host Compose project."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "graph-os"
COMPOSE = ROOT / "deploy" / "compose"
SWARM = ROOT / "deploy" / "swarm" / "stack.yml"


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
    # The broader glob packages every skill asset (SKILL.md, references/*.md,
    # and the non-Markdown agents/*.yaml files the bundled skills also ship),
    # not just Markdown.
    assert '"skills/**"' in pyproject


def test_swarm_stack_is_single_writer_stop_first_and_pinned() -> None:
    stack = yaml.safe_load(SWARM.read_text(encoding="utf-8"))
    service = stack["services"]["graph-os"]
    deploy = service["deploy"]

    assert list(stack["services"]) == ["graph-os"]
    assert deploy["replicas"] == 1
    assert deploy["update_config"]["order"] == "stop-first"
    assert deploy["rollback_config"]["order"] == "stop-first"
    assert deploy["placement"]["constraints"] == ["node.labels.graphos.data == true"]
    assert stack["networks"]["graph-os"]["driver"] == "overlay"
    assert stack["secrets"]["graphos_service_auth_secret"] == {"external": True}
    assert service["environment"]["GRAPH_SERVICE_ENDPOINTS"] == ""
    assert service["image"].startswith("${GRAPHOS_IMAGE:?")
