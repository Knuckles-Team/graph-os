"""Identity directory operations (MCPI-10), declared for the shared registry.

Every handler runs as the verified caller; mutations require a direct human
administrator and console confirmation at the invoke chokepoint.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(StrictModel):
    pass


class Page(StrictModel):
    after: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class Search(Page):
    query: str = Field(min_length=1)


class Principal(StrictModel):
    principal_id: str


class ObjectId(StrictModel):
    id: str


class ApiKeyPage(Page):
    principal_id: str


class CreateUser(StrictModel):
    username: str
    kind: Literal["human", "service"]
    display_name: str | None = None
    email: str | None = None
    roles: list[str] = []
    groups: list[str] = []


class UpdateUser(Principal):
    username: str | None = None
    display_name: str | None = None
    email: str | None = None


class RoleUpsert(StrictModel):
    role_id: str
    name: str
    description: str | None = None
    scopes: list[str] = []
    graph_grants: list[dict[str, Any]] = []


class GroupUpsert(StrictModel):
    group_id: str
    name: str
    roles: list[str] = []
    mfa_required: bool = False


class Membership(Principal):
    group_id: str
    change: Literal["add", "remove"]


class UserRole(Principal):
    role_id: str
    change: Literal["add", "remove"]


class IdpUpsert(StrictModel):
    idp_id: str
    kind: Literal["oidc", "saml", "ldap", "scim"]
    display_name: str
    enabled: bool
    config_json: str
    secret_ref: str | None = None
    jit_policy: Literal["deny", "create", "link_by_verified_email"]
    email_domains: list[str] = []
    order: int = 0
    rules: list[dict[str, Any]] = []


class MappingDryRun(StrictModel):
    idp_id: str
    claims: dict[str, list[str]]


class MappingPreview(StrictModel):
    matched_rules: list[str]
    roles: list[str]
    groups: list[str]
    scopes: list[str]
    privileged_rules: list[str]


class Collection(StrictModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class Result(StrictModel):
    changed: bool


class UserValue(RootModel[dict[str, Any]]):
    pass


class Created(StrictModel):
    principal_id: str


_HANDLER = "graph_os.identity.admin_service.execute_identity_op"

# Search, per-session revoke and key listing depend on the EG identity-ops lane.
# SCIM-client management remains unavailable until EG defines a real operation.
_OPERATIONS: tuple[tuple[str, type[BaseModel], type[BaseModel], bool], ...] = (
    ("identity.users.list", Page, Collection, True),
    ("identity.users.search", Search, Collection, True),
    ("identity.users.get", Principal, UserValue, True),
    ("identity.users.create", CreateUser, Created, False),
    ("identity.users.update", UpdateUser, Result, False),
    ("identity.users.disable", Principal, Result, False),
    ("identity.users.enable", Principal, Result, False),
    ("identity.users.deprovision", Principal, Result, False),
    ("identity.users.unlock", Principal, Result, False),
    ("identity.users.force_logout", Principal, Result, False),
    ("identity.sessions.list", Principal, Collection, True),
    ("identity.sessions.revoke", ObjectId, Result, False),
    ("identity.api_keys.list", ApiKeyPage, Collection, True),
    ("identity.api_keys.revoke", ObjectId, Result, False),
    ("identity.roles.list", Empty, Collection, True),
    ("identity.roles.upsert", RoleUpsert, Result, False),
    ("identity.roles.remove", ObjectId, Result, False),
    ("identity.roles.change_user_role", UserRole, Result, False),
    ("identity.groups.list", Empty, Collection, True),
    ("identity.groups.upsert", GroupUpsert, Result, False),
    ("identity.groups.remove", ObjectId, Result, False),
    ("identity.groups.change_membership", Membership, Result, False),
    ("identity.idps.list", Empty, Collection, True),
    ("identity.idps.upsert", IdpUpsert, Result, False),
    ("identity.idps.remove", ObjectId, Result, False),
    ("identity.idps.mapping_dry_run", MappingDryRun, MappingPreview, True),
)


def specs() -> tuple[Any, ...]:
    """Build OpSpec records when the shared MCPI-03 registry is present."""
    from graph_os.api.registry import (
        AuditClass,
        Composite,
        Confirm,
        Effect,
        Executor,
        Idempotency,
        OpSpec,
        PrincipalRule,
        Surface,
        Verb,
    )

    all_surfaces = frozenset({Surface.MCP, Surface.HTTP, Surface.A2A})
    return tuple(
        OpSpec(
            id=op_id,
            verb=Verb.ASK if read else Verb.MANAGE,
            summary=op_id.replace("identity.", "Manage identity ").replace(".", " "),
            examples=(op_id.replace(".", " "),),
            params=params,
            result=result,
            binding=Composite(handler=_HANDLER),
            executor=Executor.CALLER,
            scopes=frozenset({"identity:read" if read else "identity:admin"}),
            effect=Effect.READ if read else Effect.ADMIN,
            principals=PrincipalRule.ANY if read else PrincipalRule.HUMAN_UNDELEGATED,
            confirm=Confirm.NONE if read else Confirm.CONSOLE,
            surfaces=all_surfaces,
            idempotency=Idempotency.NONE if read else Idempotency.KEY_REQUIRED,
            audit=AuditClass.NONE if read else AuditClass.IDENTITY_CHAIN,
        )
        for op_id, params, result, read in _OPERATIONS
    )
