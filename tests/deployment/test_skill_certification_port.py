"""GraphOS owns the bounded certification inputs and lifecycle contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from graph_os.deployment import skill_validation_assets as assets
from graph_os.deployment import skill_validation_core as core


def test_certification_assets_reject_duplicate_json_keys() -> None:
    with pytest.raises(assets.CertificationAssetError):
        assets._json_without_duplicates(b'{"case":1,"case":2}', code="duplicate")


def test_certification_assets_reject_missing_material_without_path_leak(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-material.json"
    with pytest.raises(assets.CertificationAssetError) as captured:
        assets._read_regular(path, limit=1024, code="material_unavailable")
    assert str(path) not in str(captured.value)


def test_deployment_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        core.SkillValidationDeployment.model_validate({"untrusted": True})
