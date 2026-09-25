"""The deployable skill bundle has one direct and delegated case per skill."""

from __future__ import annotations

from pathlib import Path

import yaml

from graph_os.deployment.skills import BUNDLED_SKILLS


def test_graphos_skill_bundle_and_matrix_are_closed() -> None:
    root = Path(__file__).resolve().parents[2] / "graph_os" / "deployment" / "skills"
    cases = yaml.safe_load((root / "runtime_validation.yaml").read_text())["cases"]
    assert len(BUNDLED_SKILLS) == 12
    assert len(cases) == 2 * len(BUNDLED_SKILLS)
    assert {case["skill"] for case in cases} == set(BUNDLED_SKILLS)
    assert {case["mode"] for case in cases} == {"direct", "delegated"}
    assert all((root / skill / "SKILL.md").is_file() for skill in BUNDLED_SKILLS)
    assert "agent-utilities-self-evolution" not in BUNDLED_SKILLS
