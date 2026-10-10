"""GRAPHOS-OPS-R014.1: identity admin-console mapping dry-run operation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph_os.api.ops import identity
from graph_os.api.registry import Effect, Registry, Surface

pytestmark = pytest.mark.spec("GRAPHOS-OPS-R014.1")


def _context() -> SimpleNamespace:
    return SimpleNamespace(caller=SimpleNamespace(), client=object())


def test_mapping_dry_run_op_declares_identity_admin_scope_and_read_effect() -> None:
    registry = Registry(identity.operations())
    op = registry["identity.admin.mapping_dry_run"]
    assert op.scopes == frozenset({"identity:admin"})
    assert op.effect is Effect.READ
    assert Surface.MCP in op.surfaces
    assert op.examples


@pytest.mark.asyncio
async def test_mapping_dry_run_previews_first_matching_rule_without_applying() -> None:
    op = identity.operations()[0]
    params = {
        "rules": [
            {"claim_key": "team", "roles": ["viewer"]},
            {"claim_key": "admin", "roles": ["admin"]},
        ],
        "claims": {"admin": True, "team": None},
    }
    result = await identity.handle_mapping_dry_run(_context(), params, op)
    # "team" is falsy in claims, so the "admin" rule wins.
    assert result == {"roles": ["admin"]}


@pytest.mark.asyncio
async def test_mapping_dry_run_returns_no_roles_when_nothing_matches() -> None:
    op = identity.operations()[0]
    params = {"rules": [{"claim_key": "team", "roles": ["viewer"]}], "claims": {}}
    result = await identity.handle_mapping_dry_run(_context(), params, op)
    assert result == {"roles": []}


def test_registry_factory_includes_identity_in_curated_modules() -> None:
    import graph_os.api.ops.registry_factory as registry_factory_module
    import inspect

    source = inspect.getsource(registry_factory_module.get_registry)
    assert "identity" in source
