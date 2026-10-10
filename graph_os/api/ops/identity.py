"""Identity admin-console preview operations for the hosted operation registry.

GRAPHOS-OPS-R014.1: the smallest real slice of ``GRAPHOS-OPS-R014`` (identity
self-service and admin operations) -- projecting the existing, already-tested
:mod:`graph_os.identity.admin_console` dry-run preview through the same
operation registry, identity and error-handling chokepoint every other
GraphOS operation uses (see :mod:`graph_os.api.ops.access` for the sibling
pattern). ``identity.admin.mapping_dry_run`` declares ``identity:admin`` and
``Effect.READ``: it only previews the roles a mapping-rule set would assign
for the ``providers`` admin-console tab, it never applies them.

The remaining ``GRAPHOS-OPS-R014`` surface -- self-service and admin
management of users, sessions, API keys, service accounts, roles, and
groups; identity-provider policy and mode transitions; issuer rotation;
audit export and verification; and SCIM client management -- is tracked as
``GRAPHOS-OPS-R014.2`` in ``specs/hosted-api-operations/requirements.md`` and
is not implemented here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph_os.api.registry import (
    AuditClass,
    Composite,
    Effect,
    OpSpec,
    Surface,
    Verb,
)
from graph_os.identity.admin_console import AdminConsoleTab, dry_run_mapping


class MappingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_key: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(min_length=1, max_length=32)


class MappingDryRunParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[MappingRule] = Field(min_length=1, max_length=64)
    claims: dict[str, Any] = Field(default_factory=dict)


class MappingDryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str]


async def handle_mapping_dry_run(
    context: Any, params: Mapping[str, Any], op: OpSpec
) -> dict[str, Any]:
    """Preview the roles a mapping-rule set would assign; never applies them."""
    tab = AdminConsoleTab(name="providers")
    rules = [(rule["claim_key"], frozenset(rule["roles"])) for rule in params["rules"]]
    roles = dry_run_mapping(tab, rules, dict(params.get("claims", {})))
    return {"roles": sorted(roles)}


def operations() -> tuple[OpSpec, ...]:
    return (
        OpSpec(
            id="identity.admin.mapping_dry_run",
            verb=Verb.ASK,
            summary="Preview the roles a provider mapping-rule set would assign",
            examples=("preview provider mapping roles for these claims",),
            params=MappingDryRunParams,
            result=MappingDryRunResult,
            binding=Composite(
                handler="graph_os.api.ops.identity.handle_mapping_dry_run"
            ),
            scopes=frozenset({"identity:admin"}),
            effect=Effect.READ,
            audit=AuditClass.NONE,
            surfaces=frozenset({Surface.MCP, Surface.HTTP, Surface.CONSOLE}),
        ),
    )


specs = operations

__all__ = [
    "MappingDryRunParams",
    "MappingDryRunResult",
    "MappingRule",
    "handle_mapping_dry_run",
    "operations",
    "specs",
]
