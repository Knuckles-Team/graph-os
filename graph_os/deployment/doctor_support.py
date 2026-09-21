"""Shared redacted result helpers for deployment doctor checks."""

from __future__ import annotations

from typing import Any

# Status precedence (worst wins for the overall verdict).
_RANK = {"ok": 0, "skip": 0, "warn": 1, "fail": 2, "error": 2}


def _result(
    name: str,
    status: str,
    detail: str,
    *,
    remediation: str | None = None,
    skill: str | None = None,
    auto_fixable: bool = False,
    data: Any = None,
    prescription: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "remediation": remediation,
        "skill": skill,
        "auto_fixable": auto_fixable,
        "data": data,
        "prescription": prescription,
    }


def _prescription(
    *,
    manifest_path: str,
    config_keys: dict[str, str],
    gotcha: str | None = None,
    scaling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a machine-readable remediation without applying it."""
    return {
        "manifest_path": manifest_path,
        "config_keys": config_keys,
        "gotcha": gotcha,
        "scaling": scaling,
    }
