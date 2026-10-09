"""GRAPHOS-DEPLOY-R016: the host daemon runs as its own pod container.

The unified-in-process + ``engine.placement: sidecar`` profile splits the
consolidated KG host daemon out of the ``graphos`` serving container into a
dedicated ``daemon`` container in the same pod (see
``specs/portable-deployment/plan.md`` "GRAPHOS-DEPLOY-R016 plan"). The
serving container keeps the client role so it never races the daemon for
the host lock; the daemon container takes the host role through its own
env override, which wins over the shared ConfigMap's ``KG_DAEMON_ROLE``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.deployment.test_helm_extensions import _render


def _deployment(documents: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        document
        for document in documents
        if document["kind"] == "Deployment"
        and document["metadata"]["labels"].get("app.kubernetes.io/component")
        == "graphos"
    )


def _configmap_role(documents: list[dict[str, Any]]) -> str:
    configmap = next(
        document
        for document in documents
        if document["kind"] == "ConfigMap"
        and document["metadata"]["name"].endswith("-graphos")
    )
    return configmap["data"]["KG_DAEMON_ROLE"]


def _container(pod_spec: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (c for c in pod_spec["containers"] if c["name"] == name),
        None,
    )


def test_daemon_container_takes_host_role_in_sidecar_mode(tmp_path: Path) -> None:
    documents = _render(
        tmp_path,
        {"graphos": {"image": {"digest": "sha256:" + "c" * 64}}},
    )
    pod_spec = _deployment(documents)["spec"]["template"]["spec"]
    daemon = _container(pod_spec, "daemon")
    assert daemon is not None, "sidecar placement must render a daemon container"
    assert daemon["command"] == ["graph-os-daemon"]
    assert {"name": "KG_DAEMON_ROLE", "value": "host"} in daemon["env"]
    assert _configmap_role(documents) == "client", (
        "the serving container must not also claim the host role"
    )


@pytest.mark.parametrize("mode", ["child", "shared"])
def test_daemon_container_absent_outside_sidecar_mode(
    tmp_path: Path, mode: str
) -> None:
    values: dict[str, Any] = {"graphos": {"image": {"digest": "sha256:" + "d" * 64}}}
    if mode == "shared":
        values["topology"] = "out-of-process-shared"
        values["engine"] = {"tls": {"secretName": "fixture-engine-tls"}}
    else:
        values["engine"] = {"placement": "child"}
    documents = _render(tmp_path, values)
    pod_spec = _deployment(documents)["spec"]["template"]["spec"]
    assert _container(pod_spec, "daemon") is None
    # Without a dedicated daemon container the serving container (sidecar's
    # child-placement sibling) or the out-of-process client both keep their
    # pre-R016 role unchanged.
    assert _configmap_role(documents) == ("host" if mode == "child" else "client")


def test_daemon_disabled_falls_back_to_pre_r016_single_container(
    tmp_path: Path,
) -> None:
    documents = _render(
        tmp_path,
        {
            "graphos": {"image": {"digest": "sha256:" + "e" * 64}},
            "daemon": {"enabled": False},
        },
    )
    pod_spec = _deployment(documents)["spec"]["template"]["spec"]
    assert _container(pod_spec, "daemon") is None
    assert _configmap_role(documents) == "host"
