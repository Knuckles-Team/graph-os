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

from graph_os.api.invoke.steps import VerifiedCaller
from graph_os.api.registry import Effect, Registry, Surface


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


def assert_capacity_throttle_scope_split(
    registry: Registry,
) -> tuple[object, object]:
    """Shared GRAPHOS-OPS-R016 contract, asserted identically by
    ``test_capacity_ops.py`` (the full GRAPHOS-CAPACITY-R002 contract) and
    ``test_capacity_ops_r016.py`` (the R016.1 read/admin scope-split slice):
    throttle status is read-scoped, set_mode is admin-scoped, and status is
    MCP-exposed. Returns the ``(status_op, mode_op)`` pair so each caller can
    layer its own further assertions on top.
    """
    status_op = registry["capacity.throttle.status"]
    mode_op = registry["capacity.throttle.set_mode"]
    assert status_op.scopes == frozenset({"capacity:read"})
    assert status_op.effect is Effect.READ
    assert mode_op.scopes == frozenset({"capacity:admin"})
    assert mode_op.effect is Effect.ADMIN
    assert Surface.MCP in status_op.surfaces
    return status_op, mode_op


__all__ = ["make_verified_caller", "assert_capacity_throttle_scope_split"]
