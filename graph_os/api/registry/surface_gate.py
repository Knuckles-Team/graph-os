"""API-surface and backward-compatibility drift detection (GRAPHOS-OPS-R026).

Both pre-commit gates compare a reviewed baseline against the live
registry's current public shape. Neither gate talks to a production
registry directly -- callers project an `OpSpec` iterable (the production
one, or a synthetic one in tests) into the small, stable view these gates
care about, so the comparison logic stays legible and fully testable
without standing up the EG-backed production registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .spec import OpSpec

#: Principal rules ordered from widest (most callers qualify) to narrowest.
_PRINCIPAL_WIDTH = {
    "any": 3,
    "human": 2,
    "service_only": 2,
    "human_undelegated": 1,
}


def public_surface(ops: list[OpSpec]) -> dict[str, dict[str, Any]]:
    """Project each op's public wire shape: MCP verb, surfaces, HTTP route.

    This is the GRAPHOS-OPS-R026.1 surface: operation ids, HTTP routes, and
    the MCP verb set. Authority fields (scopes, principals) are
    deliberately excluded -- those are GRAPHOS-OPS-R026.2's concern.
    """
    surface: dict[str, dict[str, Any]] = {}
    for op in ops:
        surface[op.id] = {
            "verb": op.verb.value,
            "surfaces": sorted(s.value for s in op.surfaces),
            "http": (
                {"method": op.http.method, "path": op.http.path}
                if op.http is not None
                else None
            ),
        }
    return surface


@dataclass(frozen=True)
class SurfaceDrift:
    """GRAPHOS-OPS-R026.1 result: any undisclosed change to the public surface."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def explain(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added: {', '.join(self.added)}")
        if self.removed:
            parts.append(f"removed: {', '.join(self.removed)}")
        if self.changed:
            parts.append(f"changed shape: {', '.join(self.changed)}")
        return "; ".join(parts)


def diff_public_surface(
    baseline: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> SurfaceDrift:
    """Fail-closed comparison: any add/remove/shape-change is drift.

    A deliberate surface change is expected to update the checked-in
    baseline in the same commit; this is what makes the gate pass again.
    """
    added = tuple(sorted(set(current) - set(baseline)))
    removed = tuple(sorted(set(baseline) - set(current)))
    changed = tuple(
        sorted(
            op_id
            for op_id in set(current) & set(baseline)
            if current[op_id] != baseline[op_id]
        )
    )
    return SurfaceDrift(added=added, removed=removed, changed=changed)


@dataclass(frozen=True)
class CompatBreak:
    """GRAPHOS-OPS-R026.2 result: an incompatible narrowing of a published op."""

    removed_ops: tuple[str, ...]
    narrowed_scopes: tuple[str, ...]
    narrowed_principals: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(
            self.removed_ops or self.narrowed_scopes or self.narrowed_principals
        )

    def explain(self) -> str:
        parts = []
        if self.removed_ops:
            parts.append(f"removed: {', '.join(self.removed_ops)}")
        if self.narrowed_scopes:
            parts.append(f"narrowed scopes: {', '.join(self.narrowed_scopes)}")
        if self.narrowed_principals:
            parts.append(f"narrowed principals: {', '.join(self.narrowed_principals)}")
        return "; ".join(parts)


def diff_backward_compat(
    baseline: dict[str, OpSpec], current: dict[str, OpSpec]
) -> CompatBreak:
    """A published op must never narrow incompatibly for existing callers.

    Three incompatible narrowings are detected: removing a previously
    published op outright, requiring a scope a previously-qualifying
    caller did not need, and restricting which principal kinds may call
    an op that previously allowed a wider set.
    """
    removed = tuple(sorted(set(baseline) - set(current)))
    narrowed_scopes = tuple(
        sorted(
            op_id
            for op_id, old in baseline.items()
            if op_id in current and not current[op_id].scopes <= old.scopes
        )
    )
    narrowed_principals = tuple(
        sorted(
            op_id
            for op_id, old in baseline.items()
            if op_id in current
            and _PRINCIPAL_WIDTH[current[op_id].principals.value]
            < _PRINCIPAL_WIDTH[old.principals.value]
        )
    )
    return CompatBreak(
        removed_ops=removed,
        narrowed_scopes=narrowed_scopes,
        narrowed_principals=narrowed_principals,
    )
