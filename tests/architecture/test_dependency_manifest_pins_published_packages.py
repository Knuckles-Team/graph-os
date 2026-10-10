"""GRAPHOS-HOST-R014: the dependency manifest pins published packages.

GraphOS's split repositories -- the connector SDK, GraphOS itself, and the
graph engine -- each publish an installable package. GraphOS's own
``pyproject.toml`` must declare its dependency on each of them (and on the
former monolithic agent runtime, ``agent-utilities``) through a PEP 508
version specifier, never a ``path``/``file://`` reference into the former
combined source tree. ``[tool.uv.sources]`` is a separate, local-development
override and is intentionally excluded from this check.
"""

from __future__ import annotations

import pytest

import re
import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

SPLIT_REPOSITORY_PACKAGES = (
    "agent-utilities",
    "agent-connector-sdk",
    "epistemic-graph",
)

_DISALLOWED_PATTERNS = ("@ file://", "@ path://", "{ path", "file:///", "../")


def _all_dependency_strings(pyproject: dict) -> list[str]:
    project = pyproject.get("project", {})
    deps = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        deps.extend(group)
    return deps


@pytest.mark.spec('GRAPHOS-HOST-R014')
def test_manifest_declares_each_split_repository_package() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    deps = _all_dependency_strings(pyproject)
    for package in SPLIT_REPOSITORY_PACKAGES:
        matches = [
            d for d in deps if re.match(rf"^{re.escape(package)}(\[|[<>=!~\s]|$)", d)
        ]
        assert matches, f"{package} is not declared as a project dependency: {deps}"


@pytest.mark.spec('GRAPHOS-HOST-R014')
def test_split_repository_dependencies_use_version_specifiers_not_paths() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    deps = _all_dependency_strings(pyproject)
    for package in SPLIT_REPOSITORY_PACKAGES:
        for dep in deps:
            if not re.match(rf"^{re.escape(package)}(\[|[<>=!~\s]|$)", dep):
                continue
            assert not any(pattern in dep for pattern in _DISALLOWED_PATTERNS), (
                f"{package} dependency string must be a version specifier, "
                f"not a path into the former combined source tree: {dep!r}"
            )
            assert re.search(r"[<>=!~]=?\s*\d", dep), (
                f"{package} dependency string must pin a version floor: {dep!r}"
            )
