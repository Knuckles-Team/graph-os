#!/usr/bin/env python3
"""Repository-root hygiene gate.

Adapted from epistemic-graph's `scripts/check_root_hygiene.py`
(commit 43eee2c0f398d2da8fa9796909136a1a6bc03427), itself ported verbatim
(engine + docstrings) from agent-utilities' original — reused rather than
reinvented per this fleet's Extend-Before-Invent convention. The ENGINE below
is unchanged from the EG/AU original; the only adaptation is
`ALLOWED_DOTFILES`, which is inherently repo-specific (it enumerates THIS
repository's own tracked conventional dot-files, not a borrowed list) — see
`check_tracked_privacy.py`'s analogous note for why a borrowed allowlist
would silently scope a gate to directories/files that don't exist here.

The repo root is the first thing a reader of a public GitHub project sees, and
it is where scratch files accumulate fastest: a one-off proof artifact from a
CI experiment, a scratch note, a directory that belongs to the workspace
rather than to this package.

This gate enforces an **allowlist**, not a denylist. A denylist only catches
the junk somebody already thought of; an allowlist means a genuinely new root
entry has to be justified once, deliberately, by someone editing this file
(for a dotfile) or the sibling ``.repo-layout.toml`` manifest (for
everything else).

It reads the **tracked** file set (``git ls-files``), never the filesystem --
walking the filesystem makes a gate fire on build output and gitignored
artifacts.

Two holes this engine closes relative to a naive "dotfiles are all fine"
version:

1. Dotfiles are split: a conventional, self-describing dot-FILE
   (``.gitignore``, ``.pre-commit-config.yaml``, ...) is enumerated in
   ``ALLOWED_DOTFILES`` below; a dot-DIRECTORY (``.github``, ``.kiss``, ...)
   is repo-specific enough to need a stated reason, so it is declared in the
   manifest's ``[dirs]`` table like any other directory.
2. ``FORBIDDEN_ANYWHERE`` -- some tool-written files are ratchets that
   INSTALL THEMSELVES the moment a gate or developer runs the tool that
   writes them (``kiss check`` -> ``.kissconfig``). Being on an allowlist
   never rescues one of these; they are rejected anywhere in the tracked
   tree, not just at the root.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Container, Iterable
from dataclasses import dataclass
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_subprocess_env import (  # noqa: E402
    sanitized_git_env,
    strip_inherited_git_repository_env,
)

# A real `git commit`/`git push` exports GIT_DIR/GIT_INDEX_FILE into every
# hook subprocess it runs, and `git -C <root> ls-files` does NOT override
# them -- those env vars win over -C's path-based repository discovery.
# Strip once, process-wide, at import time, AND pass an explicit sanitized
# env= at the one call site below, so this gate can never silently resolve
# against the wrong repository (or a poisoned index) and report a
# false-clean root.
strip_inherited_git_repository_env()

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / ".repo-layout.toml"

# Dot-FILES that are conventional and self-describing -- their name alone
# tells a reader what tool owns them, so (unlike a directory, and unlike the
# non-dot files in the manifest) they don't need a stated reason. THIS SET IS
# THIS REPOSITORY'S OWN — derived from what is actually tracked at graph-os's
# root, not borrowed from AU/EG (see the module docstring).
ALLOWED_DOTFILES: frozenset[str] = frozenset(
    {
        # The manifest THIS GATE READS. A bootstrap omission: without it the
        # gate fails on its own config file, so wiring it as a blocking hook
        # would have bricked every commit in the repo.
        ".repo-layout.toml",
        ".bumpversion.cfg",  # release version bump config (bump2version)
        ".security-audit-allow.txt",  # risk-accepted Python OSV ledger (dependency-audit gate)
        ".gitignore",  # git exclusion patterns
        ".pre-commit-config.yaml",  # pre-commit hook config
    }
)

# Self-installing config files a tool writes to calibrate itself against its
# own past runs -- a ratchet that hides findings by construction the moment it
# is tracked, anywhere in the tree, not just at the root:
#   .kissconfig -- `kiss check` writes a self-calibrating config
# Being declared in the manifest never rescues one of these; see the module
# docstring.
FORBIDDEN_ANYWHERE: frozenset[str] = frozenset({".kissconfig"})


def _tracked_paths() -> list[str]:
    """All tracked paths, repo-root-relative, via ``git ls-files``.

    Resolves the repo root from this script's own location rather than
    trusting the caller's cwd (a hook can be invoked from anywhere), and passes
    a sanitized ``env=`` so an inherited GIT_DIR/GIT_INDEX_FILE from an outer
    ``git commit`` can never redirect ``-C``'s resolution to a different
    repository or a stale index.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
        env=sanitized_git_env(),
    ).stdout
    return [p for p in out.split("\0") if p]


def tracked_root_entries(paths: list[str]) -> tuple[set[str], set[str]]:
    """Return (root_files, root_dirs) from the tracked path list."""
    files: set[str] = set()
    dirs: set[str] = set()
    for path in paths:
        head, sep, _ = path.partition("/")
        if sep:
            dirs.add(head)
        else:
            files.add(head)
    return files, dirs


class ManifestError(Exception):
    """The manifest itself is missing or malformed -- not a hygiene finding,
    a setup error. Reported distinctly so a reader doesn't mistake a typo'd
    TOML table for a stray root file."""


def load_manifest() -> tuple[dict[str, str], dict[str, str]]:
    """Load ``.repo-layout.toml``'s ``[dirs]``/``[files]`` tables.

    Every value must be a non-empty string reason -- an empty or missing
    reason defeats the point of the manifest.
    """
    if not MANIFEST_PATH.exists():
        raise ManifestError(
            f"{MANIFEST_PATH} does not exist. Every repo this gate runs in "
            "needs a .repo-layout.toml declaring its tracked root entries "
            "(see check_root_hygiene.py's module docstring)."
        )
    try:
        data = tomllib.loads(MANIFEST_PATH.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{MANIFEST_PATH} is not valid TOML: {exc}") from exc

    dirs = data.get("dirs", {})
    files = data.get("files", {})

    for table_name, table in (("dirs", dirs), ("files", files)):
        if not isinstance(table, dict):
            raise ManifestError(f"{MANIFEST_PATH}: [{table_name}] must be a table")
        for name, reason in table.items():
            if not isinstance(reason, str) or not reason.strip():
                raise ManifestError(
                    f"{MANIFEST_PATH}: [{table_name}].{name} needs a non-empty "
                    "string reason, not a placeholder"
                )

    return dict(dirs), dict(files)


@dataclass(frozen=True)
class RootHygiene:
    """How the tracked repository root differs from its declared layout."""

    root_files: list[str]
    root_dirs: list[str]
    declared_dirs: dict[str, str]
    declared_files: dict[str, str]
    forbidden_hits: list[str]
    undeclared_dirs: list[str]
    stale_dirs: list[str]
    undeclared_dotfiles: list[str]
    undeclared_files: list[str]
    misfiled_dotfiles: list[str]
    stale_files: list[str]

    def clean(self) -> bool:
        return not (
            self.forbidden_hits
            or self.undeclared_dirs
            or self.stale_dirs
            or self.undeclared_dotfiles
            or self.undeclared_files
            or self.misfiled_dotfiles
            or self.stale_files
        )


def _sorted_absent(names: Iterable[str], present: Container[str]) -> list[str]:
    """The names not present in `present`, sorted."""

    return sorted(name for name in names if name not in present)


def inspect_root(
    paths: list[str], declared_dirs: dict[str, str], declared_files: dict[str, str]
) -> RootHygiene:
    """Compare the tracked root against the manifest without reporting anything."""

    root_files, root_dirs = tracked_root_entries(paths)
    root_dotfiles = {f for f in root_files if f.startswith(".")}
    # A manifest [files] entry only ever justifies a NON-dot file (dot-files go
    # through ALLOWED_DOTFILES instead) -- a dot-file accidentally declared in the
    # manifest would be silently ignored by the undeclared-file check below, which
    # would hide a class mismatch rather than report it.
    declared_dotfiles = {f for f in declared_files if f.startswith(".")}
    return RootHygiene(
        root_files=sorted(root_files),
        root_dirs=sorted(root_dirs),
        declared_dirs=declared_dirs,
        declared_files=declared_files,
        # FORBIDDEN_ANYWHERE: scan the WHOLE tracked tree, not just the root --
        # these ratchets install themselves wherever the tool that writes them
        # is run, and being nested somewhere plausible-looking is not a defense.
        forbidden_hits=sorted(p for p in paths if Path(p).name in FORBIDDEN_ANYWHERE),
        undeclared_dirs=_sorted_absent(root_dirs, declared_dirs),
        stale_dirs=_sorted_absent(declared_dirs, root_dirs),
        undeclared_dotfiles=_sorted_absent(
            root_dotfiles, set(ALLOWED_DOTFILES) | set(declared_files)
        ),
        undeclared_files=_sorted_absent(
            set(root_files) - root_dotfiles, declared_files
        ),
        misfiled_dotfiles=sorted(declared_dotfiles),
        stale_files=_sorted_absent(set(declared_files) - declared_dotfiles, root_files),
    )


_VIOLATION_LINES = (
    ("undeclared_dirs", "  DIR   {}/  (undeclared)"),
    ("undeclared_files", "  FILE  {}  (undeclared)"),
    ("undeclared_dotfiles", "  DOTFILE  {}  (not in ALLOWED_DOTFILES or the manifest)"),
    (
        "misfiled_dotfiles",
        "  FILE  {}  (a dot-file was declared in [files]; add it to\n"
        "           ALLOWED_DOTFILES in check_root_hygiene.py instead)",
    ),
    ("stale_dirs", "  DIR   {}/  (declared in {manifest} but no longer tracked)"),
    ("stale_files", "  FILE  {}  (declared in {manifest} but no longer tracked)"),
)


def report_violations(hygiene: RootHygiene) -> None:
    """Print every violation with the remedy that applies to it."""

    print("FAIL: repository-root hygiene violations.\n")

    if hygiene.forbidden_hits:
        print("  Self-installing ratchet config tracked anywhere in the tree:")
        for path in hygiene.forbidden_hits:
            print(f"    FORBIDDEN  {path}")
        print(
            "    -> delete it and stop tracking it; add its name to .gitignore\n"
            "       if it is not already there.\n"
        )

    for field, template in _VIOLATION_LINES:
        for entry in getattr(hygiene, field):
            print(template.format(entry, manifest=MANIFEST_PATH.name))

    if (
        hygiene.undeclared_dirs
        or hygiene.undeclared_files
        or hygiene.undeclared_dotfiles
    ):
        print(
            "\nPick the one that is true for each undeclared entry:\n"
            "  * it is scratch/proof output   -> delete it (it should never have been committed)\n"
            "  * it belongs to the workspace  -> move it to ${WORKSPACE_ROOT}/, not this package\n"
            "  * it belongs inside a package  -> move it under the package source dir or scripts/\n"
            "  * it genuinely belongs at root -> add a one-line reason to .repo-layout.toml\n"
            "    (dirs/files) or, for a conventional self-describing dot-file, to\n"
            "    ALLOWED_DOTFILES in scripts/check_root_hygiene.py\n"
        )
    if hygiene.stale_dirs or hygiene.stale_files:
        print(
            "\nA declared .repo-layout.toml entry no longer exists in the tracked tree.\n"
            "Remove it from the manifest -- a stale entry is exactly the fiction this\n"
            "manifest exists to prevent (see its own header).\n"
        )


def main() -> int:
    try:
        declared_dirs, declared_files = load_manifest()
    except ManifestError as exc:
        print(f"FAIL: {exc}")
        return 1

    hygiene = inspect_root(_tracked_paths(), declared_dirs, declared_files)
    if not hygiene.clean():
        report_violations(hygiene)
        return 1
    print(
        f"root hygiene: clean ({len(hygiene.root_files)} root files, "
        f"{len(hygiene.root_dirs)} root dirs, {len(declared_dirs)} declared dirs, "
        f"{len(declared_files)} declared files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
