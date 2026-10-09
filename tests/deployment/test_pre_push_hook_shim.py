"""Hook-audit (GRAPHOS-DEPLOY-R003): the generated pre-push shim really gates.

``scripts/bootstrap.sh`` installs the pre-push hook with
``uvx pre-commit install --hook-type pre-push``. The generated shim hard-codes
``--config=.pre-commit-config.yaml`` at install time (the documented risk: a
relocated configuration path or a missing installation must never let a push
through unchecked). These tests install that exact shim into a disposable git
repository and invoke it directly -- never this repository's own hooks, and
never a real ``git push`` -- to prove it executes the configured gate and
fails loudly rather than silently passing when its configuration is gone.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("uvx") is None, reason="uvx is required to install the real hook"
)

_ZERO_SHA = "0" * 40


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _push_stdin(repo: Path) -> str:
    """One outgoing-ref line, the shape git feeds a real pre-push hook."""
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    return f"refs/heads/main {head} refs/heads/main {_ZERO_SHA}\n"


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    """A disposable git repo with a local hook that fails and announces itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "hook-audit@example.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "hook-audit"], cwd=repo)
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "- repo: local\n"
        "  hooks:\n"
        "  - id: audit-marker-gate\n"
        "    name: audit-marker-gate\n"
        "    entry: 'false'\n"
        "    language: system\n"
        "    stages: [pre-push]\n"
        "    always_run: true\n"
        "    verbose: true\n"
    )
    (repo / "README.md").write_text("hook-audit fixture\n")
    _run(["git", "add", "README.md", ".pre-commit-config.yaml"], cwd=repo)
    commit = _run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    assert commit.returncode == 0, commit.stderr
    installed = _run(
        ["uvx", "pre-commit", "install", "--hook-type", "pre-push"], cwd=repo
    )
    assert installed.returncode == 0, installed.stderr
    return repo


@pytest.mark.spec("GRAPHOS-DEPLOY-R003")
def test_generated_shim_matches_the_known_template(hook_repo: Path) -> None:
    shim = hook_repo / ".git" / "hooks" / "pre-push"
    text = shim.read_text(encoding="utf-8")
    assert shim.stat().st_mode & 0o111, "the generated shim must be executable"
    assert "--config=.pre-commit-config.yaml" in text
    assert "--hook-type=pre-push" in text


@pytest.mark.spec("GRAPHOS-DEPLOY-R003")
def test_shim_runs_the_configured_gate_and_fails_closed(hook_repo: Path) -> None:
    shim = hook_repo / ".git" / "hooks" / "pre-push"
    result = subprocess.run(
        [str(shim), "origin", "https://example.invalid/repo.git"],
        cwd=hook_repo,
        input=_push_stdin(hook_repo),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "a failing configured gate must block the push"
    assert "audit-marker-gate" in result.stdout + result.stderr


@pytest.mark.spec("GRAPHOS-DEPLOY-R003")
def test_shim_fails_loudly_when_config_is_relocated(hook_repo: Path) -> None:
    shim = hook_repo / ".git" / "hooks" / "pre-push"
    config = hook_repo / ".pre-commit-config.yaml"
    config.rename(hook_repo / ".pre-commit-config.yaml.moved")
    result = subprocess.run(
        [str(shim), "origin", "https://example.invalid/repo.git"],
        cwd=hook_repo,
        input=_push_stdin(hook_repo),
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, (
        "a relocated/missing configuration must fail the push, never pass "
        f"silently (output: {output!r})"
    )
    assert output.strip(), "the failure must be visible, not a silent nonzero exit"
