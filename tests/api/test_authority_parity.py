"""GRAPHOS-OPS-R027 / GRAPHOS-FLEET-R017: the authority-parity oracle across
principal, operation and serving surface.

``tests/api/authority_matrix.yaml`` enumerates every (principal rule, op
surfaces/scopes, caller) combination this fixture covers and the single
correct allow/deny outcome. This test asserts the registry's real
authorization chokepoint -- surface membership plus
``graph_os.api.registry.authorized`` -- reaches exactly that outcome for
every row, so a change to either check cannot silently diverge for one
principal kind, operation or surface while looking correct for another.

This module consolidates two independently-authored matrices (one inline
synthetic probe of the ``authorized()`` chokepoint alone from
``GRAPHOS-OPS-R027``, one additionally asserting serving-surface membership
from a checked-in YAML fixture from ``GRAPHOS-FLEET-R017``) into a single
fixture and test function. Every case from both lineages is kept; cases
that were semantically identical across the two are deduped to one row
carrying both specs' ``@pytest.mark.spec`` bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    OpSpec,
    PrincipalRule,
    Surface,
    Verb,
    authorized,
)

MATRIX_PATH = Path(__file__).resolve().parent / "authority_matrix.yaml"


class _Input(BaseModel):
    pass


class _Output(BaseModel):
    ok: bool


@dataclass(frozen=True)
class _FakeCaller:
    effective_scopes: frozenset[str]
    principal_kind: str
    delegated: bool = False


def _allow(_op: OpSpec, _caller: Any) -> bool:
    return True


def _deny(_op: OpSpec, _caller: Any) -> bool:
    return False


_POLICIES = {"allow": _allow, "deny": _deny}


def _load_matrix() -> list[dict[str, Any]]:
    return yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))


def _case_id(case: dict[str, Any]) -> str:
    return case["name"]


def _case_param(case: dict[str, Any]) -> Any:
    marks = [pytest.mark.spec(spec_id) for spec_id in case.get("specs", ())]
    return pytest.param(case, id=_case_id(case), marks=marks)


@pytest.mark.parametrize("case", [_case_param(case) for case in _load_matrix()])
def test_authority_matrix_row_matches_expected_outcome(case: dict[str, Any]) -> None:
    op = OpSpec(
        id=f"authority.matrix.{case['name'].replace('-', '_')}",
        verb=Verb.ACT,
        summary="Authority matrix probe operation",
        examples=("Probe the authority chokepoint",),
        params=_Input,
        result=_Output,
        binding=Composite(handler="graph_os.identity.admin_service.probe"),
        principals=PrincipalRule(case["principal_rule"]),
        scopes=frozenset(case["op_scopes"]),
        surfaces=frozenset(Surface(s) for s in case["op_surfaces"]),
        effect=Effect.WRITE,
        audit=AuditClass.EVENT,
    )
    caller = _FakeCaller(
        effective_scopes=frozenset(case["caller_scopes"]),
        principal_kind=case["caller_kind"],
        delegated=case["caller_delegated"],
    )
    surface = Surface(case["caller_surface"])
    policy = _POLICIES[case["policy"]]

    served = surface in op.surfaces
    actual = served and authorized(op, caller, policy=policy)

    assert actual is case["expected"], case["name"]


@pytest.mark.spec("GRAPHOS-OPS-R027")
def test_authority_matrix_fixture_covers_every_principal_rule() -> None:
    covered = {case["principal_rule"] for case in _load_matrix()}
    assert covered == {rule.value for rule in PrincipalRule}


@pytest.mark.spec("GRAPHOS-OPS-R027")
def test_authority_matrix_fixture_covers_every_caller_kind() -> None:
    covered = {case["caller_kind"] for case in _load_matrix()}
    assert {"human", "service"} <= covered
