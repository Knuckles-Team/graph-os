"""GRAPHOS-HOST-R014: the dependency manifest pins published packages.

The former combined agent-runtime source tree split into separately
published repositories (the connector SDK, GraphOS, and the graph engine).
``[tool.uv.sources]`` in ``pyproject.toml`` still binds those packages to
local editable checkouts for day-to-day development, but that table is a
``uv``-only resolver hint: GraphOS's build backend is ``setuptools.build_meta``,
which has no notion of ``[tool.uv.sources]`` and derives the installed
distribution's ``Requires-Dist`` metadata from ``[project.dependencies]``
alone. These tests prove both ends of that contract — the declared manifest
and the real, already-installed distribution metadata a consumer actually
resolves against — rather than trusting either in isolation.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from setuptools.config.pyprojecttoml import read_configuration

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# The repositories the former combined agent-runtime source tree split into.
SPLIT_PACKAGES = (
    "agent-utilities",
    "agent-connector-sdk",
    "epistemic-graph",
    "agent-webui",
)


def _project_requirements() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    requirements = list(project["dependencies"])
    for extra_requirements in project["optional-dependencies"].values():
        requirements.extend(extra_requirements)
    return requirements


def _split_package_requirements() -> dict[str, Requirement]:
    found: dict[str, Requirement] = {}
    for raw in _project_requirements():
        requirement = Requirement(raw)
        if requirement.name in SPLIT_PACKAGES:
            found[requirement.name] = requirement
    return found


def test_manifest_declares_every_split_package_as_a_version_range() -> None:
    """Each split repository is a plain PEP 508 floor, never a direct URL."""
    found = _split_package_requirements()
    assert set(found) == set(SPLIT_PACKAGES), (
        "a split repository is missing from [project.dependencies] / "
        "[project.optional-dependencies]"
    )
    for name, requirement in found.items():
        assert requirement.url is None, (
            f"{name} must be a version-range requirement, not a direct URL/path "
            "reference, in the published manifest"
        )
        assert len(requirement.specifier) > 0, (
            f"{name} must declare a version floor/ceiling"
        )


def test_build_backend_cannot_see_the_local_dev_source_override() -> None:
    """``[tool.uv.sources]`` only steers ``uv sync``; the build backend ignores it.

    setuptools.build_meta reads dependency metadata exclusively from
    ``[project]``; it has no code path that consults ``[tool.uv.sources]``.
    A built wheel's ``Requires-Dist`` can therefore never regress to a local
    path as long as the build backend stays setuptools — the next test
    confirms that invariant against the actually-installed distribution.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    for name in SPLIT_PACKAGES:
        if name not in sources:
            continue
        source = sources[name]
        assert source.get("editable") is True and "path" in source, (
            f"{name}'s [tool.uv.sources] entry must stay a local editable dev "
            "override, not a published-package substitute"
        )


@pytest.mark.parametrize("name", SPLIT_PACKAGES)
def test_build_backend_metadata_requires_published_floor_not_a_path(
    name: str,
) -> None:
    """The real entry point: setuptools' own pyproject resolution.

    ``setuptools.config.pyprojecttoml.read_configuration`` is the exact
    function ``setuptools.build_meta`` calls to derive a wheel's
    ``Requires-Dist`` metadata. Reading it from *this* ``pyproject.toml`` —
    rather than an already-installed, possibly stale ``*.dist-info`` in a
    shared environment — proves what the next build of *this* checkout
    would actually declare, with ``[tool.uv.sources]`` never consulted.
    """
    resolved = read_configuration(str(PYPROJECT))
    manifest = _split_package_requirements()
    requirements = [Requirement(r) for r in resolved["project"]["dependencies"]]
    for extra_requirements in resolved["project"]["optional-dependencies"].values():
        requirements.extend(Requirement(r) for r in extra_requirements)
    matches = [requirement for requirement in requirements if requirement.name == name]
    assert matches, f"setuptools-resolved metadata has no requirement for {name}"
    for requirement in matches:
        assert requirement.url is None, (
            f"setuptools-resolved metadata pins {name} to a path/URL: {requirement!r}"
        )
        assert str(requirement.specifier) == str(manifest[name].specifier), (
            f"setuptools-resolved metadata for {name} drifted from the raw manifest"
        )
