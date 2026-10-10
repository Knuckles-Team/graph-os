"""GRAPHOS-OPS-R026.2: the backward-compatibility drift gate fails closed
when a previously published operation is removed or narrowed."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    OpSpec,
    PrincipalRule,
    Verb,
    diff_backward_compat,
)


class _Input(BaseModel):
    user_id: str


class _Output(BaseModel):
    ok: bool


def _op(op_id: str = "identity.users.disable", **changes: Any) -> OpSpec:
    values: dict[str, Any] = {
        "id": op_id,
        "verb": Verb.MANAGE,
        "summary": "Disable a user",
        "examples": ("Disable this user account",),
        "params": _Input,
        "result": _Output,
        "binding": Composite(handler="graph_os.identity.admin_service.disable_user"),
        "scopes": frozenset({"identity:admin"}),
        "effect": Effect.ADMIN,
        "principals": PrincipalRule.HUMAN,
        "audit": AuditClass.IDENTITY_CHAIN,
    }
    values.update(changes)
    return OpSpec(**values)


def _by_id(ops: list[OpSpec]) -> dict[str, OpSpec]:
    return {op.id: op for op in ops}


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_no_break_when_nothing_changes() -> None:
    baseline = _by_id([_op()])
    current = _by_id([_op()])
    result = diff_backward_compat(baseline, current)
    assert not result
    assert result.explain() == ""


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_gate_fails_when_a_published_operation_is_removed() -> None:
    baseline = _by_id([_op(), _op("identity.users.enable")])
    current = _by_id([_op()])
    result = diff_backward_compat(baseline, current)
    assert result
    assert result.removed_ops == ("identity.users.enable",)


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_gate_fails_when_a_new_required_scope_is_added() -> None:
    baseline = _by_id([_op(scopes=frozenset({"identity:admin"}))])
    current = _by_id(
        [_op(scopes=frozenset({"identity:admin", "identity:superuser"}))]
    )
    result = diff_backward_compat(baseline, current)
    assert result
    assert result.narrowed_scopes == ("identity.users.disable",)
    assert "narrowed scopes" in result.explain()


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_widening_required_scopes_is_not_a_break() -> None:
    # Dropping a required scope only widens who may call -- compatible.
    baseline = _by_id(
        [_op(scopes=frozenset({"identity:admin", "identity:superuser"}))]
    )
    current = _by_id([_op(scopes=frozenset({"identity:admin"}))])
    result = diff_backward_compat(baseline, current)
    assert not result.narrowed_scopes


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_gate_fails_when_principal_rule_is_narrowed() -> None:
    baseline = _by_id([_op(principals=PrincipalRule.ANY, scopes=frozenset())])
    current = _by_id(
        [_op(principals=PrincipalRule.HUMAN_UNDELEGATED, scopes=frozenset())]
    )
    result = diff_backward_compat(baseline, current)
    assert result
    assert result.narrowed_principals == ("identity.users.disable",)


@pytest.mark.spec("GRAPHOS-OPS-R026.2")
def test_widening_principal_rule_is_not_a_break() -> None:
    baseline = _by_id(
        [_op(principals=PrincipalRule.HUMAN_UNDELEGATED, scopes=frozenset())]
    )
    current = _by_id([_op(principals=PrincipalRule.ANY, scopes=frozenset())])
    result = diff_backward_compat(baseline, current)
    assert not result.narrowed_principals
