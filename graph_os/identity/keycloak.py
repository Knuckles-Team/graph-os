"""Keycloak as an ordinary OIDC IdP: the homelab preset and link migration (IDM-12).

**Preset.** :func:`keycloak_preset` turns today's realm -- its realm roles and
groups, including the partial imports (``elevation-approvers``,
``graph-os-capacity``, ``finance-domain-and-service``,
``live-order-approvers``) -- into one engine ``IdpConfig`` plus the roles and
groups its mapping rules target. Realm roles map 1:1 to same-named engine
roles carrying the same-named registry scope; groups map 1:1 to same-named
engine groups. The scope registry's class decides what may be mapped at all:

* ``user`` / ``domain`` realm roles -> ordinary rules;
* ``admin`` realm roles -> rules marked ``privileged`` (the engine requires an
  MFA identity-admin session to install one);
* ``approver`` realm roles -> NOT mapped: an approver scope is held only
  through its built-in group, so the group rule carries it;
* ``service-only`` realm roles -> NOT mapped: service accounts keep Keycloak
  client credentials and EG trusts Keycloak directly for ``kind=service``
  principals (IDM-02); a human can never reach such a scope.

**Migration.** Each Keycloak ``sub`` that already owns data (or appears in
RBAC) becomes that person's principal id VERBATIM, with a link row
``(idp_id, sub)``. No owned datum is rewritten; :func:`plan_link_migration`
reports the ownership changes it would make (always zero) and is a pure
dry-run; :func:`apply_link_migration` is idempotent (an existing user or link
is counted, never duplicated).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from graph_os.fleet.remote_oauth_broker import _default_broker_http_client
from graph_os.identity.idp_common import (
    IdentityPort,
    IdentityRefused,
    call_expect,
    identity_op,
    refusal_code,
)
from graph_os.identity.oidc import OidcSettings

__all__ = [
    "BUILTIN_GROUPS",
    "KeycloakAdminUsers",
    "KeycloakPreset",
    "KeycloakUser",
    "LinkMigrationPlan",
    "MigrationReport",
    "apply_link_migration",
    "keycloak_preset",
    "load_principals",
    "plan_link_migration",
]

BUILTIN_GROUPS = frozenset(
    {
        "administrators",
        "elevation-approvers",
        "live-order-approvers",
        "schema-approvers",
    }
)
"""Engine built-in groups; a rule into one is always ``privileged``."""

_IDENTIFIER = re.compile(r"^[a-z0-9._:-]{1,256}$")
_ROLE_TREATMENT = {
    "user": "map",
    "domain": "map",
    "admin": "privileged",
    "approver": "held only through its built-in approver group",
    "service-only": "service accounts keep Keycloak client credentials (IDM-02)",
}
_PAGE = 500


@dataclass(frozen=True)
class KeycloakPreset:
    """Everything an administrator installs for the homelab realm."""

    idp: dict[str, Any]
    roles: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    skipped: Mapping[str, str]


def _role_rule(role: str, privileged: bool) -> dict[str, Any]:
    return {
        "rule_id": f"kc.role.{role}",
        "claim_path": "realm_access.roles",
        "match_kind": "equals",
        "value": role,
        "target": f"role:{role}",
        "privileged": privileged,
    }


def _group_rule(group: str) -> dict[str, Any]:
    return {
        "rule_id": f"kc.group.{group}",
        "claim_path": "groups",
        "match_kind": "equals",
        "value": group,
        "target": f"group:{group}",
        "privileged": group in BUILTIN_GROUPS,
    }


def _map_roles(
    realm_roles: Iterable[str],
    scope_classes: Mapping[str, str],
    skipped: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    for role in sorted(set(realm_roles)):
        treatment = _ROLE_TREATMENT.get(
            scope_classes.get(role, ""), "not in the scope registry"
        )
        if not _IDENTIFIER.match(role):
            treatment = "not an engine identifier"
        if treatment not in ("map", "privileged"):
            skipped[f"role:{role}"] = treatment
            continue
        rules.append(_role_rule(role, treatment == "privileged"))
        roles.append({"role_id": role, "name": role, "scopes": [role]})
    return rules, roles


def _map_groups(
    groups: Iterable[str], skipped: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules: list[dict[str, Any]] = []
    upserts: list[dict[str, Any]] = []
    for group in sorted({g.removeprefix("/") for g in groups}):
        if not _IDENTIFIER.match(group):
            skipped[f"group:{group}"] = (
                "not an engine identifier (nested or mixed-case path)"
            )
            continue
        rules.append(_group_rule(group))
        if group not in BUILTIN_GROUPS:
            upserts.append({"group_id": group, "name": group, "roles": []})
    return rules, upserts


def keycloak_preset(
    *,
    idp_id: str,
    realm_url: str,
    client_id: str,
    redirect_uri: str,
    realm_roles: Iterable[str],
    groups: Iterable[str],
    scope_classes: Mapping[str, str],
    secret_ref: str | None = None,
    post_logout_redirect_uri: str | None = None,
) -> KeycloakPreset:
    """The ``keycloak-realm-roles`` preset for one realm (JIT off by default:
    people are linked by :func:`apply_link_migration` or provisioned)."""
    settings = OidcSettings(
        issuer=realm_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        claim_paths=(
            "realm_access.roles",
            "groups",
            "email",
            "email_verified",
            "preferred_username",
        ),
        group_paths=("groups",),
        post_logout_redirect_uri=post_logout_redirect_uri,
    )
    skipped: dict[str, str] = {}
    role_rules, roles = _map_roles(realm_roles, scope_classes, skipped)
    group_rules, group_upserts = _map_groups(groups, skipped)
    idp = {
        "idp_id": idp_id,
        "kind": "oidc",
        "display_name": "Keycloak",
        "enabled": True,
        "config_json": settings.model_dump_json(exclude_none=True),
        "secret_ref": secret_ref,
        "jit_policy": "deny",
        "email_domains": [],
        "order": 0,
        "rules": role_rules + group_rules,
    }
    return KeycloakPreset(idp, tuple(roles), tuple(group_upserts), skipped)


# ---------------------------------------------------------------------------
# Link migration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KeycloakUser:
    """One realm user as the Keycloak admin API lists it."""

    subject: str
    username: str
    email: str | None = None
    display_name: str | None = None
    service_account: bool = False

    @classmethod
    def from_admin_api(cls, raw: Mapping[str, Any]) -> KeycloakUser:
        names = [raw.get("firstName"), raw.get("lastName")]
        display = " ".join(str(n) for n in names if n) or None
        return cls(
            subject=str(raw["id"]),
            username=str(raw.get("username", raw["id"])),
            email=raw.get("email"),
            display_name=display,
            service_account=bool(raw.get("serviceAccountClientId"))
            or str(raw.get("username", "")).startswith("service-account-"),
        )


@dataclass
class LinkMigrationPlan:
    """The dry-run: what :func:`apply_link_migration` would send."""

    idp_id: str
    create: list[dict[str, Any]] = field(default_factory=list)
    link: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ownership_changes(self) -> int:
        """Owners whose principal id would differ after the migration."""
        return sum(1 for row in self.link if row["principal_id"] != row["subject"])


def _create_request(user: KeycloakUser) -> dict[str, Any]:
    request: dict[str, Any] = {
        "username": user.username,
        "kind": "human",
        "principal_id": user.subject,
    }
    if user.email:
        request["email"] = user.email
    if user.display_name:
        request["display_name"] = user.display_name
    return request


def _skip_reason(
    owner: str,
    user: KeycloakUser | None,
    names: Mapping[str, str],
    existing: Mapping[str, Any],
) -> str | None:
    if user is None:
        return "not a Keycloak user of this realm (left to its own authority)"
    if user.service_account:
        return "Keycloak service account (EG keeps trusting Keycloak for services)"
    holder = names.get(user.username.casefold())
    if owner not in existing and holder not in (None, owner):
        return f"username {user.username!r} already belongs to {holder}"
    return None


def plan_link_migration(
    idp_id: str,
    keycloak_users: Iterable[KeycloakUser],
    owners: Iterable[str],
    existing: Mapping[str, Mapping[str, Any]],
) -> LinkMigrationPlan:
    """Plan the migration of every data-owning Keycloak subject.

    ``owners`` are the principal ids that own data or hold an RBAC identity
    today; ``existing`` maps principal id -> the engine's ``UserView``.
    """
    by_subject = {user.subject: user for user in keycloak_users}
    names = {str(view["username"]).casefold(): pid for pid, view in existing.items()}
    plan = LinkMigrationPlan(idp_id)
    for owner in sorted(set(owners)):
        user = by_subject.get(owner)
        reason = _skip_reason(owner, user, names, existing)
        if reason is not None or user is None:
            plan.skipped[owner] = reason or "unknown"
            continue
        if owner not in existing:
            plan.create.append(_create_request(user))
        plan.link.append({"idp_id": idp_id, "subject": owner, "principal_id": owner})
    return plan


async def load_principals(port: IdentityPort) -> dict[str, dict[str, Any]]:
    """Every principal the engine store holds (``user.list``, paged)."""
    views: dict[str, dict[str, Any]] = {}
    after: str | None = None
    while True:
        query: dict[str, Any] = {"limit": _PAGE} | ({"after": after} if after else {})
        page = await call_expect(port, identity_op("user", "list", query), "users")
        views.update({str(view["principal_id"]): view for view in page})
        if len(page) < _PAGE:
            return views
        after = str(page[-1]["principal_id"])


@dataclass
class MigrationReport:
    created: int = 0
    linked: int = 0
    already_present: int = 0


async def _send_idempotent(port: IdentityPort, op: dict[str, Any]) -> bool:
    """``True`` when the op changed state, ``False`` when it already held."""
    try:
        await port.call(op)
    except IdentityRefused as refusal:
        if refusal_code(refusal) != "collision":
            raise
        return False
    return True


async def apply_link_migration(
    port: IdentityPort, plan: LinkMigrationPlan
) -> MigrationReport:
    """Send the plan. Re-running it changes nothing (collisions are counted)."""
    report = MigrationReport()
    for request in plan.create:
        if await _send_idempotent(port, identity_op("user", "create", request)):
            report.created += 1
        else:
            report.already_present += 1
    for request in plan.link:
        if await _send_idempotent(port, identity_op("idp", "link", request)):
            report.linked += 1
        else:
            report.already_present += 1
    return report


class KeycloakAdminUsers:
    """The realm's users from the Keycloak admin REST API (bounded pages)."""

    def __init__(
        self,
        base_url: str,
        realm: str,
        token: str,
        *,
        http_client_factory: Callable[[], httpx.Client] = _default_broker_http_client,
        page_size: int = 100,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("the Keycloak admin API must be reached over https")
        self._url = f"{base_url.rstrip('/')}/admin/realms/{realm}/users"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        self._http = http_client_factory
        self._page = page_size

    def users(self, limit: int = 100_000) -> list[KeycloakUser]:
        users: list[KeycloakUser] = []
        with self._http() as client:
            while len(users) < limit:
                params: dict[str, str | int] = {"first": len(users), "max": self._page}
                response = client.get(self._url, params=params, headers=self._headers)
                response.raise_for_status()
                page = response.json()
                users.extend(KeycloakUser.from_admin_api(raw) for raw in page)
                if len(page) < self._page:
                    break
        return users
