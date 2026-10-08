"""Skills served from THIS process's own installed packages.

Covers :mod:`graph_os.fleet.local_skill_catalog` — the local counterpart to
the fleet skill harvest (``tests/fleet/test_multiplexer_skills_over_mcp.py``):
every entry here is resolved from an installed package via an injected
``provider_dirs`` resolver (never the real ``agent_utilities.core.providers.
resolve_skill_provider_dirs``, so this stays hermetic), and every entry is
built in the exact catalog shape (``name``/``uri``/``description``/
``instructions``) a harvested skill is.
"""

from __future__ import annotations

from pathlib import Path

from graph_os.fleet.local_skill_catalog import (
    build_local_skill_catalog,
    core_pack_names,
    missing_core_pack_names,
)


def _write_skill(
    root: Path, name: str, *, description: str = "", body: str = ""
) -> Path:
    """One ``<name>/SKILL.md`` fixture under ``root``, returning its directory."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n"
    (skill_dir / "SKILL.md").write_text(frontmatter + (body or f"# {name}\n"))
    return skill_dir


def test_entry_point_style_provider_lists_its_two_fixture_skills(
    tmp_path: Path,
) -> None:
    """A provider resolved the way an ``agent_utilities.skill_providers``
    entry point is (one ``(provider, skill_root)`` pair per skill) is listed."""
    one = _write_skill(tmp_path, "fixture-one", description="first fixture skill")
    two = _write_skill(tmp_path, "fixture-two", description="second fixture skill")

    entries, problems = build_local_skill_catalog(
        provider_dirs=lambda: [("fixture-provider", one), ("fixture-provider", two)]
    )

    assert problems == []
    names = {entry["name"] for entry in entries}
    assert names == {"fixture-one", "fixture-two"}
    one_entry = next(e for e in entries if e["name"] == "fixture-one")
    assert one_entry["uri"] == "skill://fixture-one/SKILL.md"
    assert one_entry["description"] == "first fixture skill"


def test_universal_skills_style_nested_path_source_is_listed(tmp_path: Path) -> None:
    """universal-skills resolves skills several directories deep
    (``<category>/<name>/SKILL.md``); this module only needs the final
    ``(provider, skill_root)`` pair agent-utilities' own resolver already
    produces, so a nested root is no different from a flat one."""
    nested_root = tmp_path / "finance-workflows" / "alpha-discovery"
    nested_root.mkdir(parents=True)
    (nested_root / "SKILL.md").write_text(
        "---\nname: alpha-discovery\ndescription: find alpha\n---\n# Alpha\n"
    )

    entries, problems = build_local_skill_catalog(
        provider_dirs=lambda: [("universal-skills", nested_root)]
    )

    assert problems == []
    assert entries == [
        {
            "name": "alpha-discovery",
            "uri": "skill://alpha-discovery/SKILL.md",
            "description": "find alpha",
            "instructions": "---\nname: alpha-discovery\ndescription: find alpha\n---\n# Alpha\n",
        }
    ]


def test_malformed_skill_is_reported_and_others_still_load(tmp_path: Path) -> None:
    """One unreadable/oversized skill must not sink the whole catalog build."""
    good = _write_skill(tmp_path, "good-skill", description="loads fine")
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("x" * (512 * 1024 + 1))  # over the body bound

    entries, problems = build_local_skill_catalog(
        provider_dirs=lambda: [("fixture-provider", good), ("fixture-provider", bad)]
    )

    assert [e["name"] for e in entries] == ["good-skill"]
    assert len(problems) == 1
    assert "bad-skill" in problems[0]


def test_frontmatter_description_falls_back_to_folded_block(tmp_path: Path) -> None:
    """A real SKILL.md commonly folds its description with YAML ``>-``."""
    skill_dir = tmp_path / "folded-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: folded-skill\n"
        "description: >-\n"
        "  Line one of the description\n"
        "  continues on line two.\n"
        "---\n"
        "# Folded\n"
    )

    entries, problems = build_local_skill_catalog(
        provider_dirs=lambda: [("fixture-provider", skill_dir)]
    )

    assert problems == []
    assert entries[0]["description"] == (
        "Line one of the description continues on line two."
    )


def test_core_pack_names_are_always_present_without_a_query(tmp_path: Path) -> None:
    """A fixture catalog mirroring the real core-pack set reports nothing
    missing — the list is read unconditionally, never behind a search term."""
    names = core_pack_names()
    assert names  # the real graph_os/skills/core-pack.txt is non-empty

    fixture_providers = [
        ("fixture-provider", _write_skill(tmp_path, name)) for name in names
    ]
    entries, problems = build_local_skill_catalog(
        provider_dirs=lambda: fixture_providers
    )

    assert problems == []
    assert missing_core_pack_names(entries, names) == []


def test_missing_core_pack_name_is_reported(tmp_path: Path) -> None:
    """A core-pack name no installed provider resolves is reported, not
    silently dropped — and the OTHER names are unaffected."""
    entries, _problems = build_local_skill_catalog(
        provider_dirs=lambda: [
            ("fixture-provider", _write_skill(tmp_path, "present-skill"))
        ]
    )

    missing = missing_core_pack_names(entries, ["present-skill", "absent-skill"])

    assert missing == ["absent-skill"]
