"""Pin ``scripts/check_core_skills.py`` against two tiny fixture skills.

Mirrors ``tests/test_check_wiring.py``'s convention: run the script exactly as
the pre-commit hook would, as a subprocess against a throwaway repository
root built under ``tmp_path``, never the real repository tree.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_core_skills.py"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _build_repo(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """
        [project]
        name = "fixture"
        [project.scripts]
        fixture-tool = "fixture.cli:main"
        """,
    )
    _write(root / "fixture" / "cli.py", "def main() -> None:\n    pass\n")
    _write(root / "scripts" / "helper.sh", "#!/bin/sh\necho ok\n")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_a_passing_skill_is_clean(tmp_path: Path) -> None:
    _build_repo(tmp_path)
    skill = tmp_path / "passing" / "SKILL.md"
    _write(
        skill,
        """
        ---
        name: passing
        skill_type: skill
        description: A tiny fixture skill whose references all resolve.
        ---

        Read `fixture/cli.py` for the entrypoint, then run
        `scripts/helper.sh` once, or call `fixture-tool --check` directly.
        """,
    )

    result = _run("--repo-root", str(tmp_path), str(skill))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_failing_skill_reports_each_stale_reference(tmp_path: Path) -> None:
    _build_repo(tmp_path)
    skill = tmp_path / "failing" / "SKILL.md"
    _write(
        skill,
        """
        ---
        name: failing
        skill_type: skill
        description: A tiny fixture skill with a renamed path and command.
        ---

        Read `fixture/renamed_cli.py` for the entrypoint, then run
        `ghost-tool --check` to verify it.
        """,
    )

    result = _run("--repo-root", str(tmp_path), str(skill))

    assert result.returncode == 1
    assert "path not found: `fixture/renamed_cli.py`" in result.stdout
    assert "command not found: `ghost-tool`" in result.stdout


def test_a_bare_filename_resolves_anywhere_in_the_tree(tmp_path: Path) -> None:
    """A naming convention like ``spec.md`` need not live at the repo root."""

    _build_repo(tmp_path)
    _write(tmp_path / "nested" / "dir" / "spec.md", "# spec\n")
    skill = tmp_path / "bare" / "SKILL.md"
    _write(
        skill,
        """
        ---
        name: bare
        skill_type: skill
        description: A tiny fixture skill naming a recurring filename.
        ---

        Every owner directory has a `spec.md` describing its contract.
        """,
    )

    result = _run("--repo-root", str(tmp_path), str(skill))

    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_repo_root_is_a_setup_error(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    _write(skill, "---\nname: x\nskill_type: skill\ndescription: x\n---\nbody\n")

    result = _run("--repo-root", str(tmp_path / "does-not-exist"), str(skill))

    assert result.returncode == 2
