"""GraphOS's typed expectation of the ``agent-webui`` package layout.

RF-ADR-009 host-composition-boundary R013 ("Front ends import only public
GraphOS/agent-runtime surfaces") requires a package-layout check on the
web UI package. This module is GraphOS's side of that check: a typed
expectation of which top-level ``agent_webui`` modules GraphOS imports
from (``browser_control``, ``identity`` and ``webui_host`` wiring), and a
validator that refuses an installed-package fixture missing any of them.

The validator is exercised against an *installed-package fixture* here --
a stand-in for ``importlib.metadata``/module-inventory data -- never
against the real ``agent-webui`` install, which belongs to that package's
own test suite.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The published distribution name GraphOS depends on.
AGENT_WEBUI_DISTRIBUTION = "agent-webui"

#: The top-level ``agent_webui`` modules GraphOS imports from, per the
#: current import sites in ``graph_os/browser_control``,
#: ``graph_os/identity`` and ``graph_os/webui_host``.
EXPECTED_TOP_LEVEL_MODULES: tuple[str, ...] = (
    "agent_webui.api_extensions",
    "agent_webui.browser_control",
    "agent_webui.oidc_session",
    "agent_webui.orchestrator_model",
    "agent_webui.server",
)


class AgentWebUiLayoutError(ValueError):
    """Raised when an installed-package fixture does not satisfy GraphOS's layout expectation."""


@dataclass(frozen=True)
class InstalledPackageFixture:
    """A fixture standing in for an installed package's module inventory.

    This is deliberately not the real ``agent-webui`` install: it lets
    GraphOS's layout expectation be exercised, including its refusal
    path, without depending on that package being installed.
    """

    distribution: str
    top_level_modules: tuple[str, ...]


def validate_agent_webui_layout(fixture: InstalledPackageFixture) -> None:
    """Refuse a fixture for the wrong distribution or missing an expected module."""

    if fixture.distribution != AGENT_WEBUI_DISTRIBUTION:
        raise AgentWebUiLayoutError(
            f"fixture is for distribution {fixture.distribution!r}, "
            f"not {AGENT_WEBUI_DISTRIBUTION!r}"
        )

    missing = [
        module
        for module in EXPECTED_TOP_LEVEL_MODULES
        if module not in fixture.top_level_modules
    ]
    if missing:
        raise AgentWebUiLayoutError(
            f"installed agent-webui package fixture is missing modules "
            f"GraphOS imports: {missing}"
        )
