"""Keycloak preset over today's realm, and the homelab link migration."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from graph_os.identity.idp_common import IdentityRefused, dry_run
from graph_os.identity.keycloak import (
    KeycloakAdminUsers,
    KeycloakUser,
    apply_link_migration,
    keycloak_preset,
    load_principals,
    plan_link_migration,
)
from tests.identity.fakes import FakeIdentityPort

# The realm as services/keycloak realm/*.partial-import.json and
# agent-webui scripts/provision_identity.py declare it today.
REALM_ROLES = [
    "kg:read", "kg:write", "kg:admin", "fleet:events",
    "rbac:approve-elevation",
    "capacity:throttle", "capacity:admin", "capacity:lease", "capacity:read",
    "finance:alerts", "finance:track", "finance:backfill", "finance:propose-order",
    "compute:finance", "timeseries:read", "timeseries:write",
    "broker:admin", "broker:publish", "broker:consume", "broker:ack",
    "security:check", "lease:read", "lease:write",
    "finance:approve-live-order", "connector:write-back",
    "default-roles-homelab", "offline_access",
]  # fmt: skip
GROUPS = ["/elevation-approvers", "/live-order-approvers", "/agent-webui-users"]

# The engine scope registry's classes for those scopes (eg-capabilities scopes.rs).
CLASSES = {
    **dict.fromkeys(["kg:read", "kg:write", "timeseries:read"], "user"),
    **dict.fromkeys(
        ["finance:alerts", "finance:track", "finance:backfill", "finance:propose-order"],
        "domain",
    ),
    **dict.fromkeys(["kg:admin"], "admin"),
    **dict.fromkeys(["rbac:approve-elevation", "finance:approve-live-order"], "approver"),
    **dict.fromkeys(
        [
            "fleet:events", "capacity:throttle", "capacity:admin", "capacity:lease",
            "capacity:read", "compute:finance", "timeseries:write", "broker:admin",
            "broker:publish", "broker:consume", "broker:ack", "security:check",
            "lease:read", "lease:write", "connector:write-back",
        ],
        "service-only",
    ),
}  # fmt: skip


def _preset() -> Any:
    return keycloak_preset(
        idp_id="keycloak",
        realm_url="https://keycloak.example/realms/homelab",
        client_id="graph-os",
        redirect_uri="https://graphos.example/auth/oidc/callback",
        realm_roles=REALM_ROLES,
        groups=GROUPS,
        scope_classes=CLASSES,
        secret_ref="apps/graph-os/keycloak-client-secret",
    )


def test_preset_maps_only_what_a_person_may_hold() -> None:
    preset = _preset()
    rules = {rule["rule_id"]: rule for rule in preset.idp["rules"]}

    mapped_roles = sorted(r["role_id"] for r in preset.roles)
    assert mapped_roles == [
        "finance:alerts", "finance:backfill", "finance:propose-order", "finance:track",
        "kg:admin", "kg:read", "kg:write", "timeseries:read",
    ]  # fmt: skip
    assert rules["kc.role.kg:admin"]["privileged"] is True
    assert rules["kc.role.kg:read"]["privileged"] is False
    for scope in ("rbac:approve-elevation", "finance:approve-live-order"):
        assert "approver group" in preset.skipped[f"role:{scope}"]
    for scope in (
        "capacity:admin",
        "fleet:events",
        "lease:write",
        "connector:write-back",
    ):
        assert "IDM-02" in preset.skipped[f"role:{scope}"]
    assert preset.skipped["role:default-roles-homelab"] == "not in the scope registry"


def test_preset_groups_keep_the_partial_imports_working() -> None:
    preset = _preset()
    rules = {rule["rule_id"]: rule for rule in preset.idp["rules"]}

    for group in ("elevation-approvers", "live-order-approvers"):
        rule = rules[f"kc.group.{group}"]
        assert (rule["claim_path"], rule["value"], rule["target"]) == (
            "groups",
            group,
            f"group:{group}",
        )
        assert rule["privileged"] is True
    assert rules["kc.group.agent-webui-users"]["privileged"] is False
    assert [g["group_id"] for g in preset.groups] == ["agent-webui-users"]
    config = json.loads(preset.idp["config_json"])
    assert config["group_paths"] == ["groups"]
    assert "realm_access.roles" in config["claim_paths"]
    assert preset.idp["jit_policy"] == "deny"


def test_preset_dry_run_for_an_approver_token() -> None:
    preset = _preset()
    engine_groups = [
        {"group_id": "elevation-approvers", "roles": ["elevation-approver"]},
        {"group_id": "agent-webui-users", "roles": []},
    ]
    engine_roles = list(preset.roles) + [
        {"role_id": "elevation-approver", "scopes": ["rbac:approve-elevation"]}
    ]
    claims = {
        "realm_access.roles": ["kg:read", "rbac:approve-elevation", "capacity:admin"],
        "groups": ["elevation-approvers"],
    }

    result = dry_run(preset.idp, claims, engine_roles, engine_groups)

    assert result.roles == ("elevation-approver", "kg:read")
    assert result.scopes == ("kg:read", "rbac:approve-elevation")
    assert result.privileged_rules == ("kc.group.elevation-approvers",)


# ---------------------------------------------------------------------------
# Link migration
# ---------------------------------------------------------------------------
USERS = [
    KeycloakUser("5b1e-alice", "alice", "alice@example.invalid", "Alice A"),
    KeycloakUser("77aa-bob", "bob"),
    KeycloakUser("9c0d-svc", "service-account-graph-os", service_account=True),
    KeycloakUser("0000-idle", "idle"),
]
OWNERS = ["5b1e-alice", "77aa-bob", "9c0d-svc", "sql-wire:actor:hmac-sha256:ab"]


def test_dry_run_links_every_owner_verbatim_with_zero_ownership_changes() -> None:
    plan = plan_link_migration("keycloak", USERS, OWNERS, existing={})

    assert plan.ownership_changes == 0
    assert [c["principal_id"] for c in plan.create] == ["5b1e-alice", "77aa-bob"]
    assert plan.create[0] == {
        "username": "alice",
        "kind": "human",
        "principal_id": "5b1e-alice",
        "email": "alice@example.invalid",
        "display_name": "Alice A",
    }
    assert [(row["subject"], row["principal_id"]) for row in plan.link] == [
        ("5b1e-alice", "5b1e-alice"),
        ("77aa-bob", "77aa-bob"),
    ]
    assert "service account" in plan.skipped["9c0d-svc"]
    assert "not a Keycloak user" in plan.skipped["sql-wire:actor:hmac-sha256:ab"]
    assert "0000-idle" not in plan.skipped  # owns nothing: JIT or later provisioning


def test_username_taken_by_another_principal_is_reported_not_forced() -> None:
    existing = {"usr:0190-local": {"principal_id": "usr:0190-local", "username": "Bob"}}

    plan = plan_link_migration("keycloak", USERS, ["77aa-bob"], existing)

    assert plan.create == [] and plan.link == []
    assert "usr:0190-local" in plan.skipped["77aa-bob"]


class _Store(FakeIdentityPort):
    """Refuses a second create of a principal or link, as the engine does."""

    def __init__(self) -> None:
        super().__init__()
        self.users: dict[str, dict[str, Any]] = {}
        self.links: set[tuple[str, str]] = set()

    async def call(self, op: Any) -> Any:
        await super().call(op)
        request = op.get("request", {})
        if (op["family"], op["op"]) == ("user", "create"):
            if request["principal_id"] in self.users:
                raise IdentityRefused("collision")
            self.users[request["principal_id"]] = dict(request)
        if (op["family"], op["op"]) == ("idp", "link"):
            key = (request["idp_id"], request["subject"])
            if key in self.links:
                raise IdentityRefused("collision")
            self.links.add(key)
        if (op["family"], op["op"]) == ("user", "list"):
            return {"kind": "users", "value": list(self.users.values())}
        return {"kind": "done", "value": {"changed": True}}


async def test_apply_is_idempotent() -> None:
    store = _Store()
    plan = plan_link_migration("keycloak", USERS, OWNERS, existing={})

    first = await apply_link_migration(store, plan)
    second = await apply_link_migration(store, plan)
    replanned = plan_link_migration(
        "keycloak", USERS, OWNERS, await load_principals(store)
    )

    assert (first.created, first.linked, first.already_present) == (2, 2, 0)
    assert (second.created, second.linked, second.already_present) == (0, 0, 4)
    assert replanned.create == [] and replanned.ownership_changes == 0


async def test_apply_surfaces_real_refusals() -> None:
    port = FakeIdentityPort()
    port.refuse("user", "create", "not_authorized")
    plan = plan_link_migration("keycloak", USERS, ["5b1e-alice"], existing={})

    with pytest.raises(IdentityRefused):
        await apply_link_migration(port, plan)


def test_admin_api_users_are_paged_and_classified() -> None:
    pages = [
        [
            {"id": "a", "username": "alice", "firstName": "Alice"},
            {"id": "b", "username": "bob"},
        ],
        [{"id": "s", "username": "service-account-x", "serviceAccountClientId": "x"}],
    ]
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.params["first"]))
        assert request.headers["authorization"] == "Bearer t0k"
        return httpx.Response(200, json=pages[len(seen) - 1])

    admin = KeycloakAdminUsers(
        "https://keycloak.example",
        "homelab",
        "t0k",
        http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        page_size=2,
    )

    users = admin.users()

    assert seen == ["0", "2"]
    assert [(u.subject, u.service_account) for u in users] == [
        ("a", False),
        ("b", False),
        ("s", True),
    ]
    assert users[0].display_name == "Alice"


def test_admin_api_requires_https() -> None:
    with pytest.raises(ValueError):
        KeycloakAdminUsers("http://keycloak.example", "homelab", "t")
