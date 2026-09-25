"""MCPI-30 requires exact, classified scopes from the IDM-05 contract."""

import pytest

from graph_os.api.registry.domain_scopes import project_domain_scopes


def _contract() -> dict[str, object]:
    classes = {
        "approvals:read": ("approver", "graph-os"),
        "approvals:decide": ("approver", "graph-os"),
        "finance:read": ("domain", "finance"),
        "fleet:read": ("user", "graph-os"),
        "fleet:control": ("admin", "graph-os"),
        "loops:read": ("user", "graph-os"),
        "loops:control": ("admin", "graph-os"),
        "ops:read": ("admin", "graph-os"),
        "ops:admin": ("admin", "graph-os"),
        "mcp:discover": ("user", "graph-os"),
        "mcp:delegate": ("user", "graph-os"),
        "mcp:admin": ("admin", "graph-os"),
    }
    return {
        "scopes": [
            {
                "scope": scope,
                "class": class_,
                "owner": owner,
                "approver_group": "action-approvers" if class_ == "approver" else None,
            }
            for scope, (class_, owner) in classes.items()
        ]
    }


def test_projects_exact_scope_classes_and_owners() -> None:
    scopes = project_domain_scopes(_contract())
    assert len(scopes) == 12
    assert {row["scope"] for row in scopes} == {
        "approvals:read",
        "approvals:decide",
        "finance:read",
        "fleet:read",
        "fleet:control",
        "loops:read",
        "loops:control",
        "ops:read",
        "ops:admin",
        "mcp:discover",
        "mcp:delegate",
        "mcp:admin",
    }


@pytest.mark.parametrize("corruption", ["missing", "wrong_class", "wrong_owner"])
def test_missing_or_misclassified_scope_fails_closed(corruption: str) -> None:
    contract = _contract()
    rows = contract["scopes"]
    assert isinstance(rows, list)
    target = next(row for row in rows if row["scope"] == "mcp:delegate")
    if corruption == "missing":
        rows.remove(target)
    elif corruption == "wrong_class":
        target["class"] = "admin"
    else:
        target["owner"] = "engine"
    with pytest.raises(ValueError, match="mcp:delegate"):
        project_domain_scopes(contract)


def test_registry_refuses_duplicates_and_bad_shape() -> None:
    contract = _contract()
    rows = contract["scopes"]
    assert isinstance(rows, list)
    rows.append(rows[0].copy())
    with pytest.raises(ValueError, match="duplicate"):
        project_domain_scopes(contract)
    with pytest.raises(ValueError, match="scopes list"):
        project_domain_scopes({})


def test_approval_scope_rejects_the_wrong_group() -> None:
    contract = _contract()
    rows = contract["scopes"]
    assert isinstance(rows, list)
    target = next(row for row in rows if row["scope"] == "approvals:decide")
    target["approver_group"] = "elevation-approvers"
    with pytest.raises(ValueError, match="approvals:decide"):
        project_domain_scopes(contract)
