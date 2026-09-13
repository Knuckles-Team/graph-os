#!/usr/bin/env python3
"""Strip git-repository-redirecting env vars before a gate shells out to git.

Copied verbatim from epistemic-graph's `scripts/_git_subprocess_env.py`
(commit ceec541457209235ce3d0e0526506bf2428dc845) — repository-agnostic.

A real ``git commit``/``git push`` exports ``GIT_DIR``/``GIT_INDEX_FILE``/
``GIT_WORK_TREE`` (and siblings) into every hook it runs, and ``git -C
<other-dir> ...`` does **not** override them -- ``-C`` only changes the
working directory; the repository these env vars name still wins over
path-based discovery. This module is the reusable primitive so a gate script
invoked *directly* as its own ``language: system`` pre-commit hook (rather
than reached only via a pytest session that already strips these) can adopt
one fix instead of copy-pasting the strip logic at every call site.
"""

from __future__ import annotations

import os

#: Mirrors the equivalent test-fixture constant in epistemic-graph/
#: agent-utilities: kept as an independent copy rather than an import, since
#: this module must stay import-safe from a bare
#: ``python3 scripts/<gate>.py`` invocation with no ``tests/`` package on
#: ``sys.path``.
_DANGEROUS_GIT_ENV_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)

#: A quieter sibling leak: ``GIT_AUTHOR_*``/``GIT_COMMITTER_*``/``GIT_CONFIG*``
#: mis-author or mis-configure a git call made without its own explicit
#: override.
_DANGEROUS_GIT_ENV_PREFIXES = ("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG")


def strip_inherited_git_repository_env() -> None:
    """Process-wide chokepoint: pop the dangerous vars from ``os.environ``
    once, at module import time, so every subsequent ``subprocess.run(["git",
    ...])`` in this process -- including ones that never pass their own
    ``env=`` -- is safe. Call this at the top of any gate script that shells
    out to git, immediately after the stdlib imports. Does nothing when these
    vars were never set (the common case outside a real ``git commit``)."""
    for name in _DANGEROUS_GIT_ENV_VARS:
        os.environ.pop(name, None)
    for name in list(os.environ):
        if name.startswith(_DANGEROUS_GIT_ENV_PREFIXES):
            os.environ.pop(name, None)


def sanitized_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default ``os.environ``) with the dangerous
    vars stripped, for a call site that wants to pass an explicit ``env=``
    rather than mutate the whole process's environment."""
    env = dict(base if base is not None else os.environ)
    for name in list(env):
        if name in _DANGEROUS_GIT_ENV_VARS or name.startswith(
            _DANGEROUS_GIT_ENV_PREFIXES
        ):
            env.pop(name, None)
    return env
