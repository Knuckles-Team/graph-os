"""Decide operations are service-only (GRAPHOS-OPS-R021.1)."""

from __future__ import annotations

from graph_os.api.invoke.steps import VerifiedCaller, principal_rule
from graph_os.api.ops import decide


def _caller(**changes):
    facts = dict(
        principal="person:one",
        tenant="tenant:one",
        effective_scopes=frozenset({"decide:commit"}),
        principal_kind="human",
        authenticated=True,
        delegated=False,
        credential_kind="session",
        policy_revision="revision:one",
        request_id="request:one",
    )
    facts.update(changes)
    facts.setdefault(
        "engine_claims",
        {
            "principal": facts["principal"],
            "tenant": facts["tenant"],
            "scopes": list(facts["effective_scopes"]),
            "policy_version": facts["policy_revision"],
            "delegation": facts["delegated"],
        },
    )
    return VerifiedCaller(**facts)


def _op():
    (op,) = decide.operations()
    return op


def test_decide_commit_is_service_only() -> None:
    op = _op()
    assert op.id == "decide.commit"
    human = _caller(principal_kind="human")
    assert principal_rule(op, human) is not None, "a human-scoped token must be refused"


def test_decide_commit_allows_a_service_principal() -> None:
    op = _op()
    service = _caller(principal_kind="service")
    assert principal_rule(op, service) is None
