"""``graph-os-identity keycloak-preset`` and ``link-migration`` (IDM-12, IDM-16).

Both default to a DRY RUN that prints what would be installed or linked;
``--apply`` sends it under the signed-in administrator's own authority.

``keycloak-preset``
    One ``IdpConfig`` for the realm plus the roles and groups its mapping
    rules target (:func:`.keycloak.keycloak_preset`). The scope classes come
    from the generated IDM-05 registry, so approver and service-only realm
    roles are never mapped onto humans.
``link-migration``
    Every non-service Keycloak user of the realm becomes (or stays) a
    principal whose id IS its Keycloak ``sub``, with a link row — so no owned
    datum changes owner. The plan's ``ownership_changes`` is printed and must
    be zero; re-running an applied migration changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .engine_ports import CallerPort
from .idp_common import identity_op
from .keycloak import (
    KeycloakAdminUsers,
    KeycloakPreset,
    KeycloakUser,
    apply_link_migration,
    keycloak_preset,
    load_principals,
    plan_link_migration,
)

if TYPE_CHECKING:
    from .cli import CliIo, Operator
    from .composition import IdentityRuntime

__all__ = ["add_keycloak_commands", "keycloak_handlers"]

OperatorOf = Callable[["IdentityRuntime", str | None, "CliIo"], Awaitable["Operator"]]
Handler = Callable[["IdentityRuntime", argparse.Namespace, "CliIo"], Awaitable[int]]


def scope_classes() -> Mapping[str, str]:
    """The generated IDM-05 scope registry (scope → class)."""
    from agent_utilities.security.scope_registry import SCOPE_CLASSES

    return SCOPE_CLASSES


def resolve_secret(reference: str) -> str:
    """A secret's value by reference (never taken from an argument)."""
    from agent_utilities.security.secrets_client import create_secrets_client

    value = create_secrets_client().resolve_ref(reference)
    if not value:
        raise PermissionError("the Keycloak admin token reference did not resolve")
    return value


def realm_users(base_url: str, realm: str, token: str) -> list[KeycloakUser]:
    """The realm's users from the Keycloak admin API (blocking; run in a thread)."""
    return KeycloakAdminUsers(base_url, realm, token).users()


def _preset_of(args: argparse.Namespace) -> KeycloakPreset:
    return keycloak_preset(
        idp_id=args.idp,
        realm_url=args.realm_url,
        client_id=args.client_id,
        redirect_uri=args.redirect_uri,
        realm_roles=args.realm_role or (),
        groups=args.group or (),
        scope_classes=scope_classes(),
        secret_ref=args.secret_ref,
    )


def _preset_ops(preset: KeycloakPreset) -> list[dict[str, Any]]:
    roles = [identity_op("access", "upsert_role", role) for role in preset.roles]
    groups = [identity_op("access", "upsert_group", group) for group in preset.groups]
    return [*roles, *groups, identity_op("idp", "upsert", preset.idp)]


async def _send_all(port: CallerPort, ops: Iterable[Mapping[str, Any]]) -> int:
    sent = 0
    for op in ops:
        await port.call(op)
        sent += 1
    return sent


def _handlers(operator: OperatorOf) -> dict[str, Handler]:
    async def caller_port(
        runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
    ) -> CallerPort:
        admin = await operator(runtime, args.as_user, io)
        return CallerPort(runtime.broker.engine, admin.graph_session)

    async def preset(
        runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
    ) -> int:
        planned = _preset_of(args)
        ops = _preset_ops(planned)
        sent = (
            await _send_all(await caller_port(runtime, args, io), ops)
            if args.apply
            else 0
        )
        io.emit(
            {
                "idp": planned.idp,
                "roles": list(planned.roles),
                "groups": list(planned.groups),
                "skipped": dict(planned.skipped),
                "applied": bool(args.apply),
                "sent": sent,
            }
        )
        return 0

    async def migrate(
        runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
    ) -> int:
        token = resolve_secret(args.admin_token_ref)
        users = await asyncio.to_thread(
            realm_users, args.keycloak_url, args.realm, token
        )
        port = await caller_port(runtime, args, io)
        owners = _owners(args.owners, users)
        plan = plan_link_migration(args.idp, users, owners, await load_principals(port))
        report = await apply_link_migration(port, plan) if args.apply else None
        io.emit(
            {
                "idp": args.idp,
                "create": len(plan.create),
                "link": len(plan.link),
                "skipped": plan.skipped,
                "ownership_changes": plan.ownership_changes,
                "applied": asdict(report) if report else None,
            }
        )
        return 0 if plan.ownership_changes == 0 else 2

    return {"keycloak-preset": preset, "link-migration": migrate}


def _owners(path: str | None, users: Iterable[KeycloakUser]) -> list[str]:
    """Owners from a JSON list of principal ids, else every realm user's ``sub``."""
    if path:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        return [str(owner) for owner in loaded]
    return [user.subject for user in users if not user.service_account]


def keycloak_handlers(operator: OperatorOf) -> dict[str, Handler]:
    """The two commands, acting under ``operator``'s authority."""
    return _handlers(operator)


def add_keycloak_commands(
    sub: Any, operator_flag: Callable[[argparse.ArgumentParser], None]
) -> None:
    preset = sub.add_parser("keycloak-preset", help="the homelab Keycloak IdP preset")
    preset.add_argument("--idp", default="keycloak")
    preset.add_argument("--realm-url", required=True)
    preset.add_argument("--client-id", required=True)
    preset.add_argument("--redirect-uri", required=True)
    preset.add_argument("--secret-ref")
    preset.add_argument("--realm-role", action="append", metavar="ROLE")
    preset.add_argument("--group", action="append", metavar="GROUP")
    migrate = sub.add_parser(
        "link-migration", help="link Keycloak subjects to principals"
    )
    migrate.add_argument("--idp", default="keycloak")
    migrate.add_argument("--keycloak-url", required=True)
    migrate.add_argument("--realm", required=True)
    migrate.add_argument("--admin-token-ref", required=True)
    migrate.add_argument(
        "--owners", metavar="FILE", help="JSON list of owning principal ids"
    )
    for command in (preset, migrate):
        command.add_argument("--apply", action="store_true")
        operator_flag(command)
