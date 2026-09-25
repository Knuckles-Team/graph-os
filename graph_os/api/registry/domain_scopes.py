"""Project GraphOS API scope requirements from EG's IDM-05 registry.

The packaged EG contract is authoritative. A missing or misclassified scope
stops API registry construction rather than granting an implicit wildcard.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TypedDict


class DomainScope(TypedDict):
    scope: str
    class_: str
    owner: str
    approver_group: str | None


# These are GraphOS's requested capabilities. EG owns the published classes.
_REQUIRED_CLASSES = {
    "approvals:read": "approver",
    "approvals:decide": "approver",
    "finance:read": "domain",
    "fleet:read": "user",
    "fleet:control": "admin",
    "loops:read": "user",
    "loops:control": "admin",
    "ops:read": "admin",
    "ops:admin": "admin",
    "mcp:discover": "user",
    "mcp:delegate": "user",
    "mcp:admin": "admin",
}
_APPROVER_GROUP = "action-approvers"


def project_domain_scopes(contract: dict[str, object]) -> tuple[DomainScope, ...]:
    """Return MCPI-30 scopes only after every exact IDM-05 declaration agrees."""

    rows = contract.get("scopes")
    if not isinstance(rows, list):
        raise ValueError("EG scope contract has no scopes list")
    indexed: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("scope"), str):
            raise ValueError("EG scope contract has a malformed entry")
        scope = row["scope"]
        if scope in indexed:
            raise ValueError(f"duplicate EG scope: {scope}")
        indexed[scope] = row
    result: list[DomainScope] = []
    for scope, expected_class in sorted(_REQUIRED_CLASSES.items()):
        row = indexed.get(scope)
        expected_owner = "finance" if scope == "finance:read" else "graph-os"
        if (
            row is None
            or row.get("class") != expected_class
            or row.get("owner") != expected_owner
        ):
            raise ValueError(f"EG scope contract is missing or misclassifies {scope}")
        expected_group = _APPROVER_GROUP if expected_class == "approver" else None
        if row.get("approver_group") != expected_group:
            raise ValueError(
                f"EG scope contract has the wrong approver group for {scope}"
            )
        result.append(
            {
                "scope": scope,
                "class_": expected_class,
                "owner": expected_owner,
                "approver_group": expected_group,
            }
        )
    return tuple(result)


def domain_scopes() -> tuple[DomainScope, ...]:
    """Load the installed EG wheel's scope contract for GraphOS API startup."""

    artifact = files("epistemic_graph").joinpath("contract/scopes.json")
    contract = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("EG scope contract must be an object")
    return project_domain_scopes(contract)
