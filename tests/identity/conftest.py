"""Shared settings for the identity broker tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _graph_policy_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """A served deployment always configures its audience and policy revision."""
    from agent_utilities.core.config import config

    monkeypatch.setattr(config, "kg_policy_version", "identity-test-policy")
    monkeypatch.setattr(config, "auth_jwt_audience", "graph-os-local")
