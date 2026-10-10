"""GRAPHOS-HOST-R012: boundary seed entries for graph-os and the connector SDK.

A registry-completeness check: the architecture component registry boundary
seed manifest this repository publishes names both graph-os and
agent-connector-sdk, and each entry declares a nonempty set of owning module
paths and a nonempty public import surface -- no coverage gap.
"""

from __future__ import annotations

import pytest

from pathlib import Path
from typing import Any

import yaml

_SEEDS_PATH = (
    Path(__file__).parents[1] / "architecture" / "component-registry-boundary-seeds.yml"
)

_REQUIRED_OWNERS = {"graph-os", "agent-connector-sdk"}


def _load_seeds() -> dict[str, Any]:
    return yaml.safe_load(_SEEDS_PATH.read_text(encoding="utf-8"))


@pytest.mark.spec('GRAPHOS-HOST-R012')
def test_boundary_seeds_file_exists_and_is_owned_by_graph_os() -> None:
    manifest = _load_seeds()
    assert manifest["schema"] == "graphos-boundary-owner-seeds/v1"
    assert manifest["owner_repository"] == "graph-os"
    assert isinstance(manifest.get("seeds"), list) and manifest["seeds"]


@pytest.mark.spec('GRAPHOS-HOST-R012')
def test_graph_os_and_connector_sdk_each_have_a_registry_entry() -> None:
    manifest = _load_seeds()
    owners = {seed["owner_repository"] for seed in manifest["seeds"]}
    missing = _REQUIRED_OWNERS - owners
    assert not missing, f"boundary seed coverage gap for: {sorted(missing)}"


@pytest.mark.spec('GRAPHOS-HOST-R012')
def test_every_seed_names_owning_modules_and_a_public_surface() -> None:
    manifest = _load_seeds()
    for seed in manifest["seeds"]:
        owner = seed["owner_repository"]
        owned = seed.get("owned_module_paths")
        surface = seed.get("public_import_surface")
        assert isinstance(owned, list) and owned, f"{owner} has no owned_module_paths"
        assert isinstance(surface, list) and surface, (
            f"{owner} has no public_import_surface"
        )
        assert all(isinstance(path, str) and path for path in owned)
        assert all(isinstance(name, str) and name for name in surface)


def test_graph_os_owned_module_paths_exist_in_this_checkout() -> None:
    """Every path graph-os claims for itself must be a real module here."""
    manifest = _load_seeds()
    repo_root = Path(__file__).parents[1]
    own_seed = next(
        seed for seed in manifest["seeds"] if seed["owner_repository"] == "graph-os"
    )
    missing = [
        path
        for path in own_seed["owned_module_paths"]
        if not (repo_root / path).exists()
    ]
    assert not missing, f"graph-os boundary seed names missing paths: {missing}"
