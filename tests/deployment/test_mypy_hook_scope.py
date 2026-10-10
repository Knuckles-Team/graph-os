"""The pre-commit mypy hook type-checks the test suite (GRAPHOS-DEPLOY-R004)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"


@pytest.mark.spec("GRAPHOS-DEPLOY-R004")
def test_mypy_hook_does_not_exclude_the_test_suite() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks = [
        (repo, hook)
        for repo in config["repos"]
        for hook in repo["hooks"]
        if hook["id"] == "mypy"
    ]
    assert hooks
    for repo, hook in hooks:
        for source in (repo, hook):
            assert "tests" not in str(source.get("exclude", ""))
            assert "files" not in source or "tests" in str(source["files"])
    assert "tests" not in str(config.get("exclude", ""))
