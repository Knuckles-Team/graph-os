#!/usr/bin/env python3
"""Freshness check for a core-pack skill's backticked paths and commands.

Scans one or more ``SKILL.md`` files for backtick-quoted repository paths (a
slash, or a known file extension) and backtick-quoted bare console commands,
then confirms each still exists in a named repository checkout:

* A path containing a slash must exist at that exact location under
  ``--repo-root``. A bare filename (a known extension, no slash) is a naming
  convention, not a literal root path (for example ``spec.md`` names a file
  that recurs under every ``specs/<id>/``), so it only needs to exist
  *somewhere* in the checkout.
* A bare multi-word command's first word must be the repository's own
  declared ``[project.scripts]`` entry, a file under its ``scripts/``
  directory, or one of a short list of generic external tools (``git``,
  ``uv``, ``python``, ...) this check does not own.

This is a freshness check, not a shell parser: it does not validate flags,
and a single bare identifier (no surrounding words) is never treated as a
command, so a plain repository or skill name mentioned in prose is not
mistaken for an invocation.

Usage::

    python3 scripts/check_core_skills.py --repo-root . graph_os/skills/*/SKILL.md
    python3 scripts/check_core_skills.py --repo-root . --list-file skills.txt

Exit 0 = clean, 1 = findings, 2 = the gate could not establish its universe.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

_PATH_EXTENSIONS = (
    "py",
    "md",
    "sh",
    "toml",
    "yaml",
    "yml",
    "json",
    "ttl",
    "cfg",
    "ini",
    "txt",
    "rs",
    "ts",
    "tsx",
)
_DISALLOWED_CHARS = frozenset("<>*${}()\"'=")
_GENERIC_COMMANDS = frozenset(
    {
        "git",
        "gh",
        "uv",
        "uvx",
        "python",
        "python3",
        "pip",
        "pytest",
        "mkdocs",
        "ruff",
        "mypy",
        "ssh",
        "mkdir",
        "cd",
        "ln",
        "type",
        "bash",
        "sh",
        "docker",
        "podman",
        "helm",
        "kubectl",
        "cargo",
        "npm",
        "pnpm",
        "node",
    }
)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(" + "|".join(_PATH_EXTENSIONS) + r")$")
_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _clean_token(raw: str) -> str | None:
    """Normalize one whitespace-split token, or ``None`` if it is not a
    plain literal (a URL, or one carrying a placeholder/quote/operator)."""

    token = raw.strip().strip(".,;:")
    if not token or token.startswith(("http://", "https://")):
        return None
    if any(ch in _DISALLOWED_CHARS for ch in token):
        return None
    return token


def _is_path_token(token: str) -> bool:
    name = token.rsplit("/", 1)[-1] if "/" in token else token
    return bool(_FILENAME_RE.match(name))


def _path_exists(repo_root: Path, token: str) -> bool:
    if "/" in token:
        return (repo_root / token.lstrip("/")).exists()
    return any(repo_root.rglob(token))


def _declared_commands(repo_root: Path) -> set[str]:
    """Every name this repository would recognize as its own command."""

    names: set[str] = set(_GENERIC_COMMANDS)
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        names.update(data.get("project", {}).get("scripts", {}))
    scripts_dir = repo_root / "scripts"
    if scripts_dir.is_dir():
        for entry in scripts_dir.iterdir():
            if entry.is_file():
                names.add(entry.name)
                names.add(entry.stem)
    return names


def _span_finding(span: str, repo_root: Path, commands: set[str]) -> str | None:
    """One backtick span's finding, or ``None`` when it checks out clean."""

    words = span.split()
    if not words:
        return None
    word = _clean_token(words[0])
    if word is None:
        return None
    if _is_path_token(word):
        if not _path_exists(repo_root, word):
            return f"path not found: `{word}`"
        return None
    if len(words) > 1 and _COMMAND_RE.match(word) and word not in commands:
        return f"command not found: `{word}`"
    return None


def check_skill(path: Path, repo_root: Path, commands: set[str]) -> list[str]:
    """Every stale backticked path or command this one skill file mentions."""

    text = path.read_text(encoding="utf-8")
    findings = []
    for span in _BACKTICK_RE.findall(text):
        finding = _span_finding(span, repo_root, commands)
        if finding is not None:
            findings.append(f"{path}: {finding}")
    return findings


def _skill_paths(skills: list[str], list_file: str | None) -> list[Path]:
    paths = [Path(item) for item in skills]
    if list_file:
        lines = Path(list_file).read_text(encoding="utf-8").splitlines()
        paths.extend(Path(line.strip()) for line in lines if line.strip())
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skills", nargs="*", help="SKILL.md files to check")
    parser.add_argument("--repo-root", required=True, help="repository checkout root")
    parser.add_argument("--list-file", help="file with one SKILL.md path per line")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        print(f"repo root does not exist: {repo_root}", file=sys.stderr)
        return 2

    skills = _skill_paths(args.skills, args.list_file)
    if not skills:
        print("no skill files given", file=sys.stderr)
        return 2

    commands = _declared_commands(repo_root)
    findings: list[str] = []
    for skill in skills:
        if not skill.is_file():
            print(f"skill file does not exist: {skill}", file=sys.stderr)
            return 2
        findings.extend(check_skill(skill, repo_root, commands))

    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} stale reference(s) found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
