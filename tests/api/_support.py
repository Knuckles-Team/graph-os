"""Shared ``VerifiedCaller`` fixture factory for ``tests/api/``.

Every module here that drives an operation through a synthetic authority
needs the same minted ``VerifiedCaller`` -- principal, tenant, credential
kind and a default scope narrow enough that only the operation under test
is authorized -- differing only in which scope that default is. Duplicating
the whole factory per module just lets the fixture drift from the real
``VerifiedCaller`` contract (``graph_os.api.invoke.steps``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.registry import AuditClass, Composite, Effect, Verb


def identity_disable_op_values(
    op_id: str = "identity.users.disable", **overrides: Any
) -> dict[str, Any]:
    """The common ``identity.users.disable`` ``OpSpec`` field contract.

    ``test_api_registry.py`` and ``test_backward_compat_drift_gate.py`` each
    build variations of this same wire-identity fixture -- differing only in
    their ``params``/``result`` models and ``principals`` rule; sharing the
    skeleton here keeps it from drifting between the two call sites.
    """

    values: dict[str, Any] = {
        "id": op_id,
        "verb": Verb.MANAGE,
        "summary": "Disable a user",
        "examples": ("Disable this user account",),
        "binding": Composite(handler="graph_os.identity.admin_service.disable_user"),
        "scopes": frozenset({"identity:admin"}),
        "effect": Effect.ADMIN,
        "audit": AuditClass.IDENTITY_CHAIN,
    }
    values.update(overrides)
    return values


def make_verified_caller(default_scope: str) -> Callable[..., VerifiedCaller]:
    """Return a ``_caller(**changes)`` factory defaulting to ``default_scope``."""

    def _caller(**changes):
        facts = dict(
            principal="person:one",
            tenant="tenant:one",
            effective_scopes=frozenset({default_scope}),
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

    return _caller
