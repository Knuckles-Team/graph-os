"""GRAPHOS-OPS-R026.1: the API-surface drift gate fails closed on drift."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api.registry import (
    Composite,
    Effect,
    HttpShape,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
    diff_public_surface,
    public_surface,
)


class _Input(BaseModel):
    user_id: str


class _Output(BaseModel):
    ok: bool


def _op(op_id: str = "identity.users.read", **changes: Any) -> OpSpec:
    values: dict[str, Any] = {
        "id": op_id,
        "verb": Verb.FIND,
        "summary": "Read a user",
        "examples": ("Look up this user",),
        "params": _Input,
        "result": _Output,
        "binding": Composite(handler="graph_os.identity.admin_service.read_user"),
        "principals": PrincipalRule.ANY,
        "effect": Effect.READ,
        "http": HttpShape(method="GET", path="/v1/identity/users/{user_id}"),
    }
    values.update(changes)
    return OpSpec(**values)


def _baseline() -> list[OpSpec]:
    return [_op(), _op("identity.users.list", verb=Verb.FIND, http=None)]


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_no_drift_when_surface_is_unchanged() -> None:
    baseline = public_surface(_baseline())
    current = public_surface(_baseline())
    drift = diff_public_surface(baseline, current)
    assert not drift
    assert drift.explain() == ""


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_gate_fails_when_an_operation_is_added_without_baseline_update() -> None:
    baseline = public_surface(_baseline())
    current = public_surface([*_baseline(), _op("identity.users.disable")])
    drift = diff_public_surface(baseline, current)
    assert drift
    assert drift.added == ("identity.users.disable",)
    assert "added: identity.users.disable" in drift.explain()


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_gate_fails_when_a_published_operation_is_removed() -> None:
    baseline = public_surface(_baseline())
    current = public_surface([_op()])
    drift = diff_public_surface(baseline, current)
    assert drift
    assert drift.removed == ("identity.users.list",)


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_gate_fails_when_a_public_shape_changes_in_place() -> None:
    baseline = public_surface(_baseline())
    # Same id, but the route moved -- a silent wire-contract break.
    current = public_surface(
        [
            _op(http=HttpShape(method="GET", path="/v2/identity/users/{user_id}")),
            _op("identity.users.list", verb=Verb.FIND, http=None),
        ]
    )
    drift = diff_public_surface(baseline, current)
    assert drift
    assert drift.changed == ("identity.users.read",)
    assert not drift.added
    assert not drift.removed


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_mcp_verb_set_change_is_detected_as_drift() -> None:
    baseline = public_surface(_baseline())
    current = public_surface(
        [_op(verb=Verb.ACT), _op("identity.users.list", verb=Verb.FIND, http=None)]
    )
    drift = diff_public_surface(baseline, current)
    assert drift.changed == ("identity.users.read",)


@pytest.mark.spec("GRAPHOS-OPS-R026.1")
def test_surface_projection_excludes_non_public_surfaces_key() -> None:
    op = _op(surfaces=frozenset({Surface.MCP}))
    projected = public_surface([op])
    assert projected["identity.users.read"]["surfaces"] == ["mcp"]
