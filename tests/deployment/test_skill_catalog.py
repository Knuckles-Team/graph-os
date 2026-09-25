"""GraphOS release digest binds exactly the deployable twelve-skill corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_os.deployment.skill_catalog import (
    ReleaseCatalogError,
    prebundled_skill_catalog,
    prebundled_skill_catalog_digest,
)
from graph_os.deployment.skills import BUNDLED_SKILLS


def test_catalog_binds_the_packaged_twelve_skills() -> None:
    root = Path(__file__).resolve().parents[2] / "graph_os" / "deployment" / "skills"
    catalog = prebundled_skill_catalog(root)
    assert catalog["entryCount"] == 12
    assert {entry["skill"] for entry in catalog["entries"]} == set(BUNDLED_SKILLS)
    assert prebundled_skill_catalog_digest(root).startswith("sha256:")


def test_catalog_rejects_wrong_membership(tmp_path: Path) -> None:
    (tmp_path / "one-skill").mkdir()
    (tmp_path / "one-skill" / "SKILL.md").write_text("# Fixture\n")
    with pytest.raises(ReleaseCatalogError, match="membership"):
        prebundled_skill_catalog(tmp_path)


def test_catalog_rejects_symlinked_root(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (tmp_path / "alias").symlink_to(root)
    with pytest.raises(ReleaseCatalogError, match="root_not_directory"):
        prebundled_skill_catalog(tmp_path / "alias")
