"""The serving distribution owns complete domain-skill coverage."""

from pathlib import Path

from agent_utilities.mcp.skill_coverage import compute_coverage, discover_skills

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "graph_os" / "skills"


def test_every_verb_has_explicit_domain_skill_coverage() -> None:
    report = compute_coverage([SKILLS_ROOT])
    assert report.covered, "The canonical tool universe must not be empty"
    assert not report.uncovered, report.uncovered
    assert not report.orphans, report.orphans
    assert not report.invalid_sidecars, report.invalid_sidecars
    assert not report.duplicates, report.duplicates


def test_external_rlm_dependency_is_explicitly_skill_covered() -> None:
    research_skill = next(
        skill
        for skill in discover_skills([SKILLS_ROOT])
        if skill.name == "graph-research-and-analysis"
    )
    assert research_skill.external_claims == ("graph_rlm",)
    assert "graph_rlm" in research_skill.claims_for(frozenset())
    assert compute_coverage([SKILLS_ROOT]).covered["graph_rlm"] == [
        "graph-research-and-analysis"
    ]


def test_runtime_skill_marks_retired_database_actions_unavailable() -> None:
    text = (SKILLS_ROOT / "graph-runtime-and-governance" / "SKILL.md").read_text()
    row = next(
        line for line in text.splitlines() if line.startswith("| `graph_configure`")
    )
    actions, notes = row.split("|")[2:4]
    assert "setup_databases" not in actions
    assert "verify_databases" not in actions
    assert "typed unavailable error" in notes
