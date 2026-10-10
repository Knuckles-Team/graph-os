"""GRAPHOS-DEPLOY-R006: the ``gateway-widgets`` extra pins a compatible floor.

``graph_os/gateway/widgets/*.py`` each call ``BaseWidget._fleet_client()``
for live data -- the served fleet admission seam, never a direct connector
Python package import (confirmed by
``tests/gateway/test_widget_fleet_delegation.py`` and, at the source level,
by ``pyproject.toml``'s own ``dependencies`` comment). The ``gateway-widgets``
extra is GraphOS's side of the floor the agent-utilities extra of the same
name pinned (retired there under AU-BOUNDARY-R001 /
GRAPHOS-HOST-R004): a connector package transitively needs
agent-connector-sdk's current, post-split module layout, never the
pre-split agent-utilities-runtime shape a removed module lived in.

These tests read the declared extra the same way PR #63
(``tests/test_dependency_manifest.py``) proved the split-repository pins:
through ``setuptools.config.pyprojecttoml.read_configuration``, the exact
function ``setuptools.build_meta`` calls to derive a wheel's
``Requires-Dist`` -- never an already-installed, possibly stale
``*.dist-info`` in a shared environment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from setuptools.config.pyprojecttoml import read_configuration

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
EXTRA = "gateway-widgets"


def _extra_requirements(project: dict[str, Any]) -> list[str]:
    optional: dict[str, list[str]] = project["optional-dependencies"]
    return list(optional[EXTRA])


def test_manifest_declares_the_gateway_widgets_extra() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    optional = data["project"]["optional-dependencies"]
    assert EXTRA in optional, "pyproject.toml must declare the gateway-widgets extra"
    requirements = [Requirement(raw) for raw in optional[EXTRA]]
    assert requirements, "gateway-widgets must pin at least one floor"
    for requirement in requirements:
        assert requirement.url is None, (
            f"{requirement.name} must be a version-range requirement, not a "
            "direct URL/path reference"
        )
        assert len(requirement.specifier) > 0, (
            f"{requirement.name} must declare a version floor"
        )


@pytest.mark.spec("GRAPHOS-DEPLOY-R006")
def test_gateway_widgets_pins_the_connector_sdk_floor() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = {
        req.name: req
        for req in (Requirement(raw) for raw in _extra_requirements(data["project"]))
    }
    assert "agent-connector-sdk" in requirements
    # Every fleet connector package is built on this SDK; pinning it here
    # (matching the base-dependency floor) is the module-layout compatibility
    # guarantee R006 asks for, honest to today's zero-direct-import widgets.
    base = next(
        Requirement(raw)
        for raw in data["project"]["dependencies"]
        if Requirement(raw).name == "agent-connector-sdk"
    )
    assert str(requirements["agent-connector-sdk"].specifier) == str(base.specifier)


def test_build_backend_metadata_matches_the_raw_manifest() -> None:
    """The real entry point: setuptools' own pyproject resolution (see PR #63)."""
    resolved = read_configuration(str(PYPROJECT))
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    resolved_requirements = [
        Requirement(r) for r in resolved["project"]["optional-dependencies"][EXTRA]
    ]
    raw_requirements = [Requirement(r) for r in _extra_requirements(raw["project"])]
    assert {r.name: str(r.specifier) for r in resolved_requirements} == {
        r.name: str(r.specifier) for r in raw_requirements
    }
    for requirement in resolved_requirements:
        assert requirement.url is None, (
            f"setuptools-resolved metadata pins {requirement.name} to a "
            f"path/URL: {requirement!r}"
        )
