"""Tests for the GRAPHOS-HOST-R001 import-boundary census."""

from __future__ import annotations

import pytest

import textwrap
from pathlib import Path

from graph_os.architecture.boundary_census import BoundaryOwner, ImportBoundaryCensus


def test_census_loads_seeds_from_the_published_registry() -> None:
    census = ImportBoundaryCensus.from_seeds_file()
    owner = census.owner_for("agent_connector_sdk.mcp")
    assert owner is not None
    assert owner.owner_repository == "agent-connector-sdk"


@pytest.mark.spec('GRAPHOS-HOST-R001')
def test_census_accepts_an_import_through_the_public_surface(tmp_path: Path) -> None:
    census = ImportBoundaryCensus.from_seeds_file()
    source = tmp_path / "allowed.py"
    source.write_text(
        textwrap.dedent(
            """
            from agent_connector_sdk.mcp import build_server
            """
        ),
        encoding="utf-8",
    )
    assert census.census(source) == []


@pytest.mark.spec('GRAPHOS-HOST-R001')
def test_census_refuses_an_import_outside_the_public_surface(tmp_path: Path) -> None:
    census = ImportBoundaryCensus.from_seeds_file()
    source = tmp_path / "leaking.py"
    source.write_text(
        textwrap.dedent(
            """
            from agent_connector_sdk.credentials.internal import _raw_secret_store
            """
        ),
        encoding="utf-8",
    )
    violations = census.census(source)
    assert len(violations) == 1
    assert violations[0].owner_repository == "agent-connector-sdk"
    assert violations[0].imported == "agent_connector_sdk.credentials.internal"


def test_owner_treats_graph_os_imports_as_self_owned() -> None:
    census = ImportBoundaryCensus.from_seeds_file()
    owner = census.owner_for("graph_os.fleet")
    assert owner is not None
    assert owner.owner_repository == "graph-os"


def test_covers_rejects_a_sibling_prefix_that_is_not_a_dotted_child() -> None:
    owner = BoundaryOwner(
        owner_repository="agent-connector-sdk",
        owned_module_paths=("agent_connector_sdk/mcp",),
        public_import_surface=("agent_connector_sdk.mcp",),
    )
    assert owner.covers("agent_connector_sdk.mcp") is True
    assert owner.covers("agent_connector_sdk.mcp_legacy") is False
