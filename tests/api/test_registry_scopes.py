"""Exported domain scope classes (GRAPHOS-OPS-R028)."""

from __future__ import annotations

import re

from graph_os.api.registry import (
    DOMAIN_SCOPE_CLASSES,
    FinanceScope,
    FleetScope,
    LoopsScope,
    OpsScope,
)

_SCOPE_RE = re.compile(r"^[a-z]+:[a-z]+$")

_EXPECTED = {
    FinanceScope: {"read"},
    FleetScope: {"read", "control"},
    LoopsScope: {"read", "control"},
    OpsScope: {"read", "admin"},
}


def test_every_declared_class_is_in_the_exported_tuple() -> None:
    assert set(DOMAIN_SCOPE_CLASSES) == set(_EXPECTED)


def test_each_scope_triple_matches_its_declared_class() -> None:
    for cls, actions in _EXPECTED.items():
        domain = cls.__name__.removesuffix("Scope").lower()
        values = {member.value for member in cls}
        assert values == {f"{domain}:{action}" for action in actions}
        for member in cls:
            assert _SCOPE_RE.match(member.value), member.value


def test_scope_strings_are_consumed_without_duplication() -> None:
    all_values = [member.value for cls in DOMAIN_SCOPE_CLASSES for member in cls]
    assert len(all_values) == len(set(all_values)), "duplicate scope string exported"
