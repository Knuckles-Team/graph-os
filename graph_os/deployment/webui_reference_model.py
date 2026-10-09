"""Typed model of GraphOS's web UI package reference sites.

RF-ADR-009 host-composition-boundary R003 ("Web UI rename cutover across
GraphOS") requires every reference to the web UI package -- import sites,
the optional ``webui`` extra, the ``uv`` package source, the release
workflow's checkout step, the deployment preflight check, and the
browser-control tests/docs -- to resolve against the renamed web UI
package. This module gives those reference sites a typed shape so a
mismatch (a stale pre-rename name) is refused rather than silently
tolerated.

Current main's names (as of the ``agent-webui`` rename): the published
distribution is ``agent-webui``, imported as the ``agent_webui`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: The renamed web UI package's published distribution name.
RENAMED_WEB_UI_DISTRIBUTION = "agent-webui"

#: The renamed web UI package's importable module name.
RENAMED_WEB_UI_MODULE = "agent_webui"

_ALLOWED_NAMES = (RENAMED_WEB_UI_DISTRIBUTION, RENAMED_WEB_UI_MODULE)


class WebUiReferenceKind(Enum):
    """The categories of web UI package reference the R003 row names."""

    IMPORT_SITE = "import_site"
    PACKAGE_EXTRA = "package_extra"
    PACKAGE_SOURCE = "package_source"
    RELEASE_CHECKOUT_STEP = "release_checkout_step"
    PREFLIGHT_CHECK = "preflight_check"
    BROWSER_CONTROL_TEST_OR_DOC = "browser_control_test_or_doc"


class StaleWebUiReferenceError(ValueError):
    """Raised when a reference site names something other than the renamed package."""


@dataclass(frozen=True)
class WebUiPackageReference:
    """One site in GraphOS that references the web UI package by name.

    Construction refuses any ``package_name`` that is not the renamed
    distribution or module name -- a reference left pointing at a
    pre-rename name is a defect, not data to be ingested quietly.
    """

    kind: WebUiReferenceKind
    site: str
    package_name: str

    def __post_init__(self) -> None:
        if self.package_name not in _ALLOWED_NAMES:
            raise StaleWebUiReferenceError(
                f"{self.site} ({self.kind.value}) references "
                f"{self.package_name!r}, not the renamed web UI package "
                f"({RENAMED_WEB_UI_DISTRIBUTION!r} / {RENAMED_WEB_UI_MODULE!r})"
            )


def current_web_ui_references() -> tuple[WebUiPackageReference, ...]:
    """The reference sites the R003 row names, as they exist on current main."""

    return (
        WebUiPackageReference(
            WebUiReferenceKind.IMPORT_SITE,
            "graph_os/browser_control/browser_control_service.py",
            RENAMED_WEB_UI_MODULE,
        ),
        WebUiPackageReference(
            WebUiReferenceKind.PACKAGE_EXTRA,
            "pyproject.toml:[project.optional-dependencies].webui",
            RENAMED_WEB_UI_DISTRIBUTION,
        ),
        WebUiPackageReference(
            WebUiReferenceKind.PACKAGE_SOURCE,
            "pyproject.toml:[tool.uv.sources].agent-webui",
            RENAMED_WEB_UI_DISTRIBUTION,
        ),
        WebUiPackageReference(
            WebUiReferenceKind.RELEASE_CHECKOUT_STEP,
            ".github/workflows/release.yml:Checkout pinned agent-webui source",
            RENAMED_WEB_UI_DISTRIBUTION,
        ),
        WebUiPackageReference(
            WebUiReferenceKind.PREFLIGHT_CHECK,
            "graph_os/deployment/preflight.py:COMPONENTS",
            RENAMED_WEB_UI_DISTRIBUTION,
        ),
        WebUiPackageReference(
            WebUiReferenceKind.BROWSER_CONTROL_TEST_OR_DOC,
            "graph_os/webui_host/webui_co_service.py",
            RENAMED_WEB_UI_MODULE,
        ),
    )
