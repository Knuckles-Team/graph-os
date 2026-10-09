"""Decide operations are service-only (GRAPHOS-OPS-R021.1)."""

from __future__ import annotations

from dataclasses import dataclass

from graph_os.api.invoke.steps import principal_rule
from graph_os.api.ops import decide


@dataclass(frozen=True)
class _Caller:
    effective_scopes: frozenset[str]
    principal_kind: str
    delegated: bool = False


def _op():
    (op,) = decide.operations()
    return op


def test_decide_commit_is_service_only() -> None:
    op = _op()
    assert op.id == "decide.commit"
    human = _Caller(
        effective_scopes=frozenset({"decide:commit"}), principal_kind="human"
    )
    assert principal_rule(op, human) is not None, "a human-scoped token must be refused"


def test_decide_commit_allows_a_service_principal() -> None:
    op = _op()
    service = _Caller(
        effective_scopes=frozenset({"decide:commit"}), principal_kind="service"
    )
    assert principal_rule(op, service) is None
