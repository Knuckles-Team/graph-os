"""Tests for the R003 web UI package reference model."""

from __future__ import annotations

import pytest

from graph_os.deployment.webui_reference_model import (
    RENAMED_WEB_UI_DISTRIBUTION,
    RENAMED_WEB_UI_MODULE,
    StaleWebUiReferenceError,
    WebUiPackageReference,
    WebUiReferenceKind,
    current_web_ui_references,
)


def test_current_references_all_name_the_renamed_package() -> None:
    references = current_web_ui_references()
    assert references
    for reference in references:
        assert reference.package_name in (
            RENAMED_WEB_UI_DISTRIBUTION,
            RENAMED_WEB_UI_MODULE,
        )


def test_every_reference_kind_is_represented() -> None:
    kinds = {reference.kind for reference in current_web_ui_references()}
    assert kinds == set(WebUiReferenceKind)


def test_stale_package_name_is_refused() -> None:
    with pytest.raises(StaleWebUiReferenceError):
        WebUiPackageReference(
            WebUiReferenceKind.IMPORT_SITE,
            "graph_os/browser_control/browser_control_service.py",
            "agent_web_ui",  # pre-rename / misspelled name
        )


def test_unrelated_package_name_is_refused() -> None:
    with pytest.raises(StaleWebUiReferenceError):
        WebUiPackageReference(
            WebUiReferenceKind.PACKAGE_EXTRA,
            "pyproject.toml:[project.optional-dependencies].webui",
            "graphos-webui",
        )
