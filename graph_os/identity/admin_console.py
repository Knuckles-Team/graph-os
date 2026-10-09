"""Typed model for admin-console identity tabs (GRAPHOS-IDENTITY-R007).

Slice .1: the typed model, construction validation, and refusal tests only.
The dry-run preview and mode wizard behaviors are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import IdentityUnavailable

_TABS = ("users", "sessions", "providers", "policy")


@dataclass(frozen=True, slots=True)
class AdminConsoleTab:
    """One identity-administration tab, named from the fixed tab set."""

    name: str

    def __post_init__(self) -> None:
        if self.name not in _TABS:
            raise IdentityUnavailable(
                f"unknown admin console tab {self.name!r}; expected one of {_TABS}"
            )
