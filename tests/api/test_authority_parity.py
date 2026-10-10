"""GRAPHOS-FLEET-R017: the authority-parity oracle across principal,
operation and serving surface.

``tests/api/authority_matrix.yaml`` enumerates every (principal rule, op
surfaces/scopes, caller) combination this fixture covers and the single
correct allow/deny outcome. This test asserts the registry's real
authorization chokepoint -- surface membership plus
``graph_os.api.registry.authorized`` -- reaches exactly that outcome for
every row, so a change to either check cannot silently diverge for one
principal kind, operation or surface while looking correct for another.

This is a sibling oracle to ``GRAPHOS-OPS-R027``'s inline matrix in the
same module name (added by PR #178, not yet on `main` at the time this
was written): that one is a synthetic probe of the ``authorized()``
chokepoint alone; this one additionally asserts serving-surface
membership from a checked-in YAML fixture. A later merge should
consolidate both matrices into one file rather than keep two.
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


@pytest.mark.spec("GRAPHOS-FLEET-R017")
@pytest.mark.parametrize("case", _load_matrix(), ids=_case_id)
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
