"""Exported domain scope classes for the GraphOS operation registry (GRAPHOS-OPS-R028).

Each class is a ``StrEnum`` whose members are the exact ``domain:action`` scope
strings GraphOS operations declare (``OpSpec.scopes`` / ``executor_scopes``) and
that the engine's scope registry must also carry, so the two stay byte-for-byte
in sync without either side hand-copying the other's string literals.
"""

from __future__ import annotations

from enum import StrEnum


class FinanceScope(StrEnum):
    READ = "finance:read"


class FleetScope(StrEnum):
    READ = "fleet:read"
    CONTROL = "fleet:control"


class LoopsScope(StrEnum):
    READ = "loops:read"
    CONTROL = "loops:control"


class OpsScope(StrEnum):
    READ = "ops:read"
    ADMIN = "ops:admin"


#: Every domain scope class this module exports, for iteration by tests and by
#: any consumer that must enumerate the full set without naming each class.
DOMAIN_SCOPE_CLASSES: tuple[type[StrEnum], ...] = (
    FinanceScope,
    FleetScope,
    LoopsScope,
    OpsScope,
)

__all__ = [
    "DOMAIN_SCOPE_CLASSES",
    "FinanceScope",
    "FleetScope",
    "LoopsScope",
    "OpsScope",
]
