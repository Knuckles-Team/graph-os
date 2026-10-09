"""Tests for the R013 agent-webui package layout expectation."""

from __future__ import annotations

import pytest

from graph_os.webui_host.package_layout_expectation import (
    AGENT_WEBUI_DISTRIBUTION,
    EXPECTED_TOP_LEVEL_MODULES,
    AgentWebUiLayoutError,
    InstalledPackageFixture,
    validate_agent_webui_layout,
)


def test_fixture_with_every_expected_module_passes() -> None:
    fixture = InstalledPackageFixture(
        distribution=AGENT_WEBUI_DISTRIBUTION,
        top_level_modules=EXPECTED_TOP_LEVEL_MODULES,
    )
    validate_agent_webui_layout(fixture)


def test_fixture_missing_a_module_is_refused() -> None:
    fixture = InstalledPackageFixture(
        distribution=AGENT_WEBUI_DISTRIBUTION,
        top_level_modules=tuple(
            module
            for module in EXPECTED_TOP_LEVEL_MODULES
            if module != "agent_webui.oidc_session"
        ),
    )
    with pytest.raises(AgentWebUiLayoutError):
        validate_agent_webui_layout(fixture)


def test_fixture_for_the_wrong_distribution_is_refused() -> None:
    fixture = InstalledPackageFixture(
        distribution="agent-web-ui",
        top_level_modules=EXPECTED_TOP_LEVEL_MODULES,
    )
    with pytest.raises(AgentWebUiLayoutError):
        validate_agent_webui_layout(fixture)
