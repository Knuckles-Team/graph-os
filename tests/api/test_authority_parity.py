"""GRAPHOS-OPS-R027: authority-parity across every principal kind and op.

An authority matrix fixture enumerates every `PrincipalRule` x caller
(principal kind, delegated) x scope-possession combination and asserts
`graph_os.api.registry.authorized` produces the one correct allow/deny
outcome for each -- so a change to the authorization chokepoint cannot
silently diverge for one principal kind while looking correct for
another.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    authorized,
)


class _Input(BaseModel):
    pass


class _Output(BaseModel):
    ok: bool


@dataclass(frozen=True)
class _FakeCaller:
    effective_scopes: frozenset[str]
    principal_kind: str
    delegated: bool = False


def _op(principals: PrincipalRule, scopes: frozenset[str] = frozenset()) -> OpSpec:
    return OpSpec(
        id="authority.matrix.probe",
        verb=Verb.ACT,
        summary="Authority matrix probe operation",
        examples=("Probe the authority chokepoint",),
        params=_Input,
        result=_Output,
        binding=Composite(handler="graph_os.identity.admin_service.probe"),
        principals=principals,
        scopes=scopes,
        effect=Effect.WRITE,
        audit=AuditClass.EVENT,
    )


def _allow(_op: OpSpec, _caller: Any) -> bool:
    return True


def _deny(_op: OpSpec, _caller: Any) -> bool:
    return False


# (op principal rule, op scopes, caller kind, caller delegated, caller scopes,
#  policy decision, expected) -- the authority matrix fixture.
_REQUIRED = frozenset({"ops:probe"})
_MATRIX: tuple[tuple[Any, ...], ...] = (
    # ANY: any principal kind qualifies once scoped and policy-allowed.
    (PrincipalRule.ANY, _REQUIRED, "human", False, _REQUIRED, _allow, True),
    (PrincipalRule.ANY, _REQUIRED, "human", True, _REQUIRED, _allow, True),
    (PrincipalRule.ANY, _REQUIRED, "service", False, _REQUIRED, _allow, True),
    (PrincipalRule.ANY, _REQUIRED, "agent", False, _REQUIRED, _allow, False),
    # HUMAN: service principals are denied outright, regardless of scope.
    (PrincipalRule.HUMAN, _REQUIRED, "human", False, _REQUIRED, _allow, True),
    (PrincipalRule.HUMAN, _REQUIRED, "human", True, _REQUIRED, _allow, True),
    (PrincipalRule.HUMAN, _REQUIRED, "service", False, _REQUIRED, _allow, False),
    # HUMAN_UNDELEGATED: a delegated human caller is denied even with scope.
    (
        PrincipalRule.HUMAN_UNDELEGATED,
        _REQUIRED,
        "human",
        False,
        _REQUIRED,
        _allow,
        True,
    ),
    (
        PrincipalRule.HUMAN_UNDELEGATED,
        _REQUIRED,
        "human",
        True,
        _REQUIRED,
        _allow,
        False,
    ),
    (
        PrincipalRule.HUMAN_UNDELEGATED,
        _REQUIRED,
        "service",
        False,
        _REQUIRED,
        _allow,
        False,
    ),
    # SERVICE_ONLY: a human caller is denied even with scope.
    (PrincipalRule.SERVICE_ONLY, _REQUIRED, "service", False, _REQUIRED, _allow, True),
    (PrincipalRule.SERVICE_ONLY, _REQUIRED, "human", False, _REQUIRED, _allow, False),
    # Missing scope denies regardless of principal kind.
    (PrincipalRule.ANY, _REQUIRED, "human", False, frozenset(), _allow, False),
    (
        PrincipalRule.SERVICE_ONLY,
        _REQUIRED,
        "service",
        False,
        frozenset(),
        _allow,
        False,
    ),
    # A denying policy always loses, even with full scope and principal match.
    (PrincipalRule.ANY, _REQUIRED, "human", False, _REQUIRED, _deny, False),
    (PrincipalRule.HUMAN, frozenset(), "human", False, frozenset(), _deny, False),
)


@pytest.mark.spec("GRAPHOS-OPS-R027")
@pytest.mark.parametrize(
    (
        "principals",
        "op_scopes",
        "caller_kind",
        "delegated",
        "caller_scopes",
        "policy",
        "expected",
    ),
    _MATRIX,
)
def test_authority_matrix_parity(
    principals: PrincipalRule,
    op_scopes: frozenset[str],
    caller_kind: str,
    delegated: bool,
    caller_scopes: frozenset[str],
    policy: Any,
    expected: bool,
) -> None:
    op = _op(principals, op_scopes)
    caller = _FakeCaller(
        effective_scopes=caller_scopes, principal_kind=caller_kind, delegated=delegated
    )
    assert authorized(op, caller, policy=policy) is expected


@pytest.mark.spec("GRAPHOS-OPS-R027")
def test_authority_matrix_fixture_covers_every_principal_rule() -> None:
    covered = {row[0] for row in _MATRIX}
    assert covered == set(PrincipalRule)


@pytest.mark.spec("GRAPHOS-OPS-R027")
def test_authority_matrix_fixture_covers_every_caller_kind() -> None:
    covered = {row[2] for row in _MATRIX}
    assert {"human", "service"} <= covered
