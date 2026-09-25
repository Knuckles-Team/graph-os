"""Caller-bound identity administration over the engine's Identity method.

Only the curated operation table below may reach this service. The API invoke
chokepoint authenticates, checks exact scopes, principal kind, and fresh MFA
before calling it; the engine repeats the authority check using the same
caller's verified context. No administrator operation borrows broker authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .engine import (
    EngineIdentityPort,
    IdentityCall,
    IdentityEngine,
    IdentityUnavailable,
)
from .idp_common import dry_run
from .issuer import Retirement

__all__ = [
    "AdminAction",
    "IdentityAdminService",
    "ADMIN_ACTIONS",
    "execute_identity_op",
]


@dataclass(frozen=True)
class AdminAction:
    family: str
    operation: str
    reply_kind: str
    read: bool = False
    page: bool = False


ADMIN_ACTIONS: dict[str, AdminAction] = {
    "identity.self.password.change": AdminAction(
        "credential", "change_password", "done"
    ),
    "identity.users.list": AdminAction("user", "list", "users", True, True),
    "identity.users.search": AdminAction("user", "search", "users", True, True),
    "identity.service_accounts.list": AdminAction(
        "user", "list_service_accounts", "users", True, True
    ),
    "identity.service_accounts.create": AdminAction("user", "create", "principal"),
    "identity.service_accounts.deprovision": AdminAction("user", "set_status", "done"),
    "identity.users.get": AdminAction("user", "get", "user", True),
    "identity.users.create": AdminAction("user", "create", "principal"),
    "identity.users.update": AdminAction("user", "update", "done"),
    "identity.users.disable": AdminAction("user", "set_status", "done"),
    "identity.users.enable": AdminAction("user", "set_status", "done"),
    "identity.users.deprovision": AdminAction("user", "set_status", "done"),
    "identity.users.unlock": AdminAction("user", "unlock", "done"),
    "identity.users.force_logout": AdminAction("session", "revoke_all", "done"),
    "identity.sessions.list": AdminAction("session", "list", "sessions", True),
    "identity.sessions.revoke": AdminAction("session", "revoke_one", "done"),
    "identity.api_keys.list": AdminAction(
        "token", "list_api_keys", "api_keys", True, True
    ),
    "identity.api_keys.revoke": AdminAction("token", "revoke_api_key", "done"),
    "identity.roles.list": AdminAction("access", "list_roles", "roles", True),
    "identity.roles.upsert": AdminAction("access", "upsert_role", "done"),
    "identity.roles.remove": AdminAction("access", "remove_role", "done"),
    "identity.roles.change_user_role": AdminAction(
        "access", "change_user_role", "done"
    ),
    "identity.groups.list": AdminAction("access", "list_groups", "groups", True),
    "identity.groups.upsert": AdminAction("access", "upsert_group", "done"),
    "identity.groups.remove": AdminAction("access", "remove_group", "done"),
    "identity.groups.change_membership": AdminAction(
        "access", "change_membership", "done"
    ),
    "identity.idps.list": AdminAction("idp", "list", "idps", True),
    "identity.idps.upsert": AdminAction("idp", "upsert", "done"),
    "identity.idps.remove": AdminAction("idp", "remove", "done"),
    "identity.scim_clients.list": AdminAction(
        "idp", "list_scim_clients", "scim_clients", True
    ),
    "identity.scim_clients.get": AdminAction(
        "idp", "get_scim_client", "scim_client", True
    ),
    "identity.scim_clients.upsert": AdminAction("idp", "upsert_scim_client", "done"),
    "identity.scim_clients.remove": AdminAction("idp", "remove_scim_client", "done"),
    "identity.policy.get": AdminAction("config", "get", "config", True),
    "identity.policy.set": AdminAction("config", "update_policy", "config"),
    "identity.mode.status": AdminAction("config", "get", "config", True),
    "identity.audit.list": AdminAction("config", "audit", "audit", True, True),
    "identity.audit.export": AdminAction("config", "export_audit", "audit", True, True),
    "identity.audit.verify": AdminAction(
        "config", "verify_audit", "audit_verification", True
    ),
}


def _request(op_id: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate friendly API fields to the exact EG request schema."""
    body = dict(params)
    if op_id in {
        "identity.users.disable",
        "identity.users.enable",
        "identity.users.deprovision",
        "identity.service_accounts.deprovision",
    }:
        status = {
            "identity.users.disable": "disabled",
            "identity.users.enable": "active",
            "identity.users.deprovision": "deprovisioned",
            "identity.service_accounts.deprovision": "deprovisioned",
        }[op_id]
        return {"principal_id": body["principal_id"], "status": status}
    if op_id == "identity.service_accounts.create":
        return {**body, "kind": "service"}
    if op_id == "identity.users.force_logout":
        return {"id": body["principal_id"]}
    if op_id in {"identity.users.get", "identity.users.unlock"}:
        return {"id": body["principal_id"]}
    if op_id in {"identity.scim_clients.get", "identity.scim_clients.remove"}:
        return {"id": body["idp_id"]}
    if op_id in {
        "identity.roles.remove",
        "identity.groups.remove",
        "identity.idps.remove",
        "identity.api_keys.revoke",
    }:
        return {"id": body["id"]}
    if op_id == "identity.sessions.list":
        return {"id": body["principal_id"]}
    if op_id in {
        "identity.roles.list",
        "identity.groups.list",
        "identity.idps.list",
        "identity.scim_clients.list",
        "identity.policy.get",
        "identity.mode.status",
        "identity.audit.verify",
    }:
        return None
    if op_id in {
        "identity.users.list",
        "identity.users.search",
        "identity.service_accounts.list",
        "identity.api_keys.list",
        "identity.audit.list",
        "identity.audit.export",
    }:
        limit = int(body.get("limit", 100))
        if not 1 <= limit <= 500:
            raise ValueError("limit must be from 1 to 500")
        result = {
            "limit": limit,
            **({"after": body["after"]} if body.get("after") else {}),
        }
        for key in ("query", "principal_id"):
            if key in body:
                result[key] = body[key]
        return result
    return body


class IdentityAdminService:
    """Typed, caller-authorized service behind identity.* Composite ops."""

    def __init__(self, engine: IdentityEngine) -> None:
        self._engine = engine

    async def execute(
        self, op_id: str, caller_session: Any, params: Mapping[str, Any]
    ) -> Any:
        action = ADMIN_ACTIONS.get(op_id)
        if action is None:
            raise IdentityUnavailable(f"identity operation {op_id!r} is unavailable")
        if caller_session is None:
            raise IdentityUnavailable(
                "identity administration requires a verified caller"
            )
        request = _request(op_id, params)
        reply = await self._engine.as_caller(
            caller_session, IdentityCall(action.family, action.operation, request)
        )
        value = reply.expect(action.reply_kind)
        if action.reply_kind in {
            "users",
            "sessions",
            "roles",
            "groups",
            "idps",
            "scim_clients",
            "audit",
            "api_keys",
        }:
            items = list(value)
            cursor = _next_cursor(op_id, items, request) if action.page else None
            return {"items": items, "next_cursor": cursor}
        return value

    async def mapping_dry_run(
        self, caller_session: Any, idp_id: str, claims: Mapping[str, list[str]]
    ) -> dict[str, list[str]]:
        """Preview a rule set using live IdPs/roles/groups, without mutation."""
        idps = await self.execute("identity.idps.list", caller_session, {})
        roles = await self.execute("identity.roles.list", caller_session, {})
        groups = await self.execute("identity.groups.list", caller_session, {})
        idp = next(
            (entry for entry in idps["items"] if entry.get("idp_id") == idp_id), None
        )
        if idp is None:
            raise IdentityUnavailable("identity provider not found")
        result = dry_run(idp, claims, roles["items"], groups["items"])
        return {
            "matched_rules": list(result.matched_rules),
            "roles": list(result.roles),
            "groups": list(result.groups),
            "scopes": list(result.scopes),
            "privileged_rules": list(result.privileged_rules),
        }


async def execute_identity_op(context: Any, params: Mapping[str, Any], op: Any) -> Any:
    """Composite handler used by the shared API invoke chokepoint.

    The execution context supplies the caller's verified graph session and
    tenant-bound EG client. Mode transitions and explicit issuer rotations use
    the identity broker to rotate signing keys before recording the EG change.
    """
    if op.id in {"identity.mode.transition", "identity.issuer.rotate"}:
        broker = context.services.get("identity")
        if broker is None:
            raise IdentityUnavailable("identity broker is unavailable")
        if op.id == "identity.issuer.rotate":
            return await _rotate_issuer(broker, context.caller.session)
        return await broker.transition(
            context.caller.session,
            params["to"],
            ack=params.get("ack"),
            local_fallback=params.get("local_fallback"),
        )
    engine = EngineIdentityPort(lambda _tenant: context.client, lambda: None)
    service = IdentityAdminService(engine)
    if op.id == "identity.idps.mapping_dry_run":
        return await service.mapping_dry_run(
            context.caller.session, params["idp_id"], params["claims"]
        )
    return await service.execute(op.id, context.caller.session, params)


async def _rotate_issuer(broker: Any, caller_session: Any) -> Any:
    """Rotate the signing ring and record its new kid under caller authority.

    Rotation keeps the old public key published for one access-token lifetime.
    If the EG write refuses, the operator may retry; the previous key remains
    valid during the overlap window. There is no cross-store rollback primitive.
    """
    config = await broker.config(fresh=True)
    kid = broker.issuer.rotate(Retirement.OVERLAP)
    try:
        reply = await broker.engine.as_caller(
            caller_session,
            IdentityCall(
                "config",
                "rotate_issuer",
                {"expected_epoch": config["epoch"], "issuer_kid": kid},
            ),
        )
    finally:
        broker.forget_config()
    return reply.expect("config")


def _next_cursor(
    op_id: str, items: list[Any], request: Mapping[str, Any] | None
) -> str | None:
    if not items or not request or len(items) < request["limit"]:
        return None
    last = items[-1]
    if not isinstance(last, Mapping):
        raise IdentityUnavailable("identity page contains an invalid record")
    key = {
        "identity.audit.list": "seq",
        "identity.audit.export": "seq",
        "identity.api_keys.list": "key_id",
    }.get(op_id, "principal_id")
    if key not in last:
        raise IdentityUnavailable("identity page lacks a cursor field")
    return str(last[key])
