"""Decide operations are service-only (GRAPHOS-OPS-R021.1)."""

from __future__ import annotations

import pytest

from graph_os.api.invoke.steps import principal_rule
from graph_os.api.ops import decide
from tests.api._support import make_verified_caller

_caller = make_verified_caller("decide:commit")


def _op():
    (op,) = decide.operations()
    return op


@pytest.mark.spec(
    "GRAPHOS-HOST-R012",
    "GRAPHOS-OPS-R021.1",
    "GRAPHOS-DEPLOY-R002",
    "GRAPHOS-DEPLOY-R005",
    "GRAPHOS-DEPLOY-R013",
)
def test_decide_commit_is_service_only() -> None:
    op = _op()
    assert op.id == "decide.commit"
    human = _caller(principal_kind="human")
    assert principal_rule(op, human) is not None, "a human-scoped token must be refused"


@pytest.mark.spec(
    "GRAPHOS-HOST-R012",
    "GRAPHOS-OPS-R021.1",
    "GRAPHOS-DEPLOY-R002",
    "GRAPHOS-DEPLOY-R005",
    "GRAPHOS-DEPLOY-R013",
)
def test_decide_commit_allows_a_service_principal() -> None:
    op = _op()
    service = _caller(principal_kind="service")
    assert principal_rule(op, service) is None
