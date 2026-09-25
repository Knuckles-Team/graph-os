"""Shape, portability and ingestion contract of the bundled Graph OS skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from graph_os.skills import GRAPHOS_SKILLS

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "graph_os" / "skills"
SKILL_DIRS = [SKILLS_ROOT / name for name in GRAPHOS_SKILLS]

# Assembled from parts so this guard's own source is not a matchable literal.
_SITE_PREFIX = "/home/" + "apps"
FORBIDDEN = (
    re.compile(re.escape(_SITE_PREFIX)),
    re.compile(r"\b10\.0\.0\.\d+\b"),
    re.compile(r"\b(?:rw?|gr)\d{3,}\b", re.IGNORECASE),
    re.compile(r"\.arpa\b"),
    re.compile(r"\bpassword\s*:", re.IGNORECASE),
)


def _frontmatter(text: str) -> dict[str, object]:
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_inventory_matches_directories() -> None:
    on_disk = sorted(
        path.name for path in SKILLS_ROOT.iterdir() if (path / "SKILL.md").is_file()
    )
    assert on_disk == sorted(GRAPHOS_SKILLS)


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=lambda p: p.name)
def test_frontmatter_declares_a_runnable_skill(skill: Path) -> None:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)

    assert set(frontmatter) == {"name", "description", "skill_type"}
    assert frontmatter["name"] == skill.name
    assert frontmatter["skill_type"] == "skill"
    assert len(text.splitlines()) < 500
    assert "\n## Workflow\n" in text


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=lambda p: p.name)
def test_local_references_are_shallow_and_resolve(skill: Path) -> None:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((references/[^)#]+)\)", text)

    assert links
    for link in links:
        relative = Path(link)
        assert len(relative.parts) == 2
        assert (skill / relative).is_file(), link
    for reference in (skill / "references").glob("*.md"):
        for sibling in re.findall(r"\]\(([A-Za-z-]+\.md)\)", reference.read_text()):
            assert (skill / "references" / sibling).is_file(), (reference, sibling)


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_contains_no_site_inventory_or_secret_values(skill: Path) -> None:
    for path in skill.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            assert pattern.search(text) is None, f"{pattern.pattern} in {path}"


def test_genesis_hands_off_to_deployment() -> None:
    text = (SKILLS_ROOT / "graphos-genesis" / "SKILL.md").read_text(encoding="utf-8")

    assert "**Mandatory delegation rule:**" in text
    assert "`graphos-deployment`" in text
    assert "substrate_resolved: true" in text
    assert "Docker Swarm is not a supported target" in text


def test_deployment_verifies_the_browser_path() -> None:
    root = SKILLS_ROOT / "graphos-deployment"
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    checklist = (root / "references" / "first-boot-verification.md").read_text(
        encoding="utf-8"
    )

    assert "`graphos-genesis`" in skill
    assert "browser sign-in" in skill
    assert "## 3. Browser sign-in path" in checklist


@pytest.mark.parametrize("skill", SKILL_DIRS, ids=lambda p: p.name)
def test_unshipped_features_are_marked_with_their_train(skill: Path) -> None:
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in skill.rglob("*.md"))
    if "GRAPHOS_AUTH_MODE" in corpus or "/auth/setup" in corpus:
        assert "train 7" in corpus


def test_ingestion_mints_runnable_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegation runs only CallableResource(AGENT_SKILL) nodes; the atomic leg mints them."""
    from agent_utilities.knowledge_graph.ingestion import (
        skill_workflow_ingest as ingest,
    )

    minted: list[tuple[str, str | None]] = []

    def _record(_engine: object, **kwargs: Any) -> str:
        minted.append((str(kwargs["name"]), kwargs.get("skill_type")))
        return f"resource:skill:{kwargs['name']}"

    monkeypatch.setattr(ingest, "ingest_runnable_skill", _record)
    report = ingest.ingest_atomic_skills(cast(Any, object()), root=str(SKILLS_ROOT))

    assert report["errors"] == 0
    assert report["not_skill"] == 0
    assert sorted(minted) == [(name, "skill") for name in sorted(GRAPHOS_SKILLS)]
