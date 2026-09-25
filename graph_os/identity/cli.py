"""``graph-os-identity``: the operator's identity commands (IDM-16).

Every command runs on the host with the deployment's own configuration and
reaches the engine identity store through the same broker the served
listeners use. Commands that change identity state run under a signed-in
ADMINISTRATOR's own authority (``--as USERNAME``; the password and, when
enrolled, the second factor are prompted, never taken from arguments), or —
in ``none`` mode only — under the bootstrap administrator:

``claim --username NAME``
    none → local: give the bootstrap administrator a username and password,
    then transition (every object the demo created stays owned by it).
``transition --to MODE [--ack TEXT] [--local-fallback off|break-glass|full]``
    Move the auth mode one edge; the issuer key rotates first.
``reset-admin --principal ID``
    A single-use reset token for an account, printed once.
``link-claim``
    A one-time, ten-minute code that links the operator's next external
    sign-in to their existing principal.
``rotate [--revoke]``
    Rotate the local issuer's signing key (``--revoke`` drops the old key at
    once instead of keeping it for one token lifetime).
``keycloak-preset`` / ``link-migration``
    The homelab Keycloak preset and link migration (:mod:`.keycloak_cli`).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .composition import IdentityRuntime
from .engine import IdentityCall, Resolution
from .issuer import Retirement
from .keycloak_cli import add_keycloak_commands, keycloak_handlers
from .modes import AUTH_MODES
from .principal_session import session_for

__all__ = ["CliIo", "Operator", "build_parser", "main", "run"]

_LINK_CLAIM_TTL_MS = 10 * 60 * 1000
_FALLBACKS = ("off", "break-glass", "full")


@dataclass(frozen=True)
class CliIo:
    """The terminal: a secret prompt and a line writer (swapped in tests)."""

    prompt: Callable[[str], str] = getpass.getpass
    write: Callable[[str], None] = print

    def emit(self, payload: Mapping[str, Any]) -> None:
        self.write(json.dumps(dict(payload), sort_keys=True))


@dataclass(frozen=True)
class Operator:
    """A signed-in principal: its store session and its own graph session."""

    session_token: str
    resolution: Resolution
    graph_session: Any


class CliRefused(RuntimeError):
    """A command could not act (the reason is printed; exit status 2)."""


async def _second_factor(runtime: IdentityRuntime, session: str, io: CliIo) -> None:
    code = io.prompt("Authenticator code: ")
    result = await runtime.broker.second_factor(session, code, recovery=False)
    if result.outcome != "ok":
        raise CliRefused("the second factor was refused")


async def _signed_in(runtime: IdentityRuntime, username: str, io: CliIo) -> str:
    opened = await runtime.broker.sign_in(
        username, io.prompt(f"Password for {username}: ")
    )
    if opened.session_token is None:
        raise CliRefused(f"sign-in refused ({opened.sign_in.outcome})")
    if opened.sign_in.outcome == "mfa_required":
        await _second_factor(runtime, opened.session_token, io)
    return opened.session_token


async def operator(
    runtime: IdentityRuntime, username: str | None, io: CliIo
) -> Operator:
    """The acting administrator: ``--as`` signs in; ``none`` mode is bootstrap."""
    if username:
        session = await _signed_in(runtime, username, io)
    elif await runtime.admission.mode() == "none":
        session = (await runtime.admission.bootstrap()).session_token
    else:
        raise CliRefused("this command needs --as USERNAME (an administrator)")
    resolution = await runtime.broker.resolve_session(session)
    if resolution is None or not resolution.usable:
        raise CliRefused("the operator session did not resolve")
    graph_session = session_for(runtime.broker, resolution, ("session",))
    return Operator(session, resolution, graph_session)


def _new_password(io: CliIo) -> str:
    first = io.prompt("New password: ")
    if first != io.prompt("Repeat the new password: "):
        raise CliRefused("the passwords differ")
    return first


async def _claim(runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo) -> int:
    if await runtime.admission.mode() != "none":
        raise CliRefused("claim moves none → local; this install is not in none mode")
    admin = await operator(runtime, None, io)
    principal = admin.resolution.principal_id
    password = _new_password(io)
    calls = (
        IdentityCall(
            "user", "update", {"principal_id": principal, "username": args.username}
        ),
        IdentityCall(
            "credential",
            "set_password",
            {"principal_id": principal, "password": password},
        ),
    )
    for call in calls:
        await runtime.broker.engine.as_caller(admin.graph_session, call)
    config = await runtime.broker.transition(admin.graph_session, "local")
    io.emit({"claimed": principal, "username": args.username, "mode": config["mode"]})
    return 0


async def _transition(
    runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
) -> int:
    admin = await operator(runtime, args.as_user, io)
    config = await runtime.broker.transition(
        admin.graph_session, args.to, ack=args.ack, local_fallback=args.local_fallback
    )
    io.emit({"mode": config["mode"], "epoch": config["epoch"]})
    return 0


async def _reset_admin(
    runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
) -> int:
    admin = await operator(runtime, args.as_user, io)
    token = await runtime.broker.issue_admin_reset(admin.session_token, args.principal)
    io.emit(
        {"principal": args.principal, "reset_token": token, "expires_in_minutes": 30}
    )
    return 0


async def _link_claim(
    runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo
) -> int:
    from .material import new_token

    admin = await operator(runtime, args.as_user, io)
    code = new_token()
    request = {
        "session_token": admin.session_token,
        "purpose": "link_claim",
        "principal_id": admin.resolution.principal_id,
        "token": code,
        "ttl_ms": _LINK_CLAIM_TTL_MS,
    }
    await runtime.broker.engine.broker(IdentityCall("token", "issue_one_time", request))
    io.emit(
        {
            "principal": admin.resolution.principal_id,
            "link_code": code,
            "expires_in_minutes": 10,
        }
    )
    return 0


async def _rotate(runtime: IdentityRuntime, args: argparse.Namespace, io: CliIo) -> int:
    retirement = Retirement.REVOKE if args.revoke else Retirement.OVERLAP
    io.emit({"kid": runtime.broker.issuer.rotate(retirement), "retirement": retirement})
    return 0


Handler = Callable[[IdentityRuntime, argparse.Namespace, CliIo], Awaitable[int]]

_HANDLERS: Mapping[str, Handler] = {
    "claim": _claim,
    "transition": _transition,
    "reset-admin": _reset_admin,
    "link-claim": _link_claim,
    "rotate": _rotate,
    **keycloak_handlers(operator),
}


def _operator_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--as", dest="as_user", metavar="USERNAME", help="administrator"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graph-os-identity", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("claim", help="none → local").add_argument(
        "--username", required=True
    )
    transition = sub.add_parser("transition", help="move the auth mode one edge")
    transition.add_argument("--to", required=True, choices=AUTH_MODES)
    transition.add_argument("--ack")
    transition.add_argument("--local-fallback", choices=_FALLBACKS)
    reset = sub.add_parser("reset-admin", help="a single-use reset token")
    reset.add_argument("--principal", required=True)
    link = sub.add_parser("link-claim", help="a one-time external link code")
    for command in (transition, reset, link):
        _operator_flag(command)
    sub.add_parser("rotate", help="rotate the issuer key").add_argument(
        "--revoke", action="store_true"
    )
    add_keycloak_commands(sub, _operator_flag)
    return parser


async def run(
    runtime: IdentityRuntime, argv: Sequence[str], io: CliIo | None = None
) -> int:
    """Parse ``argv`` and run one command against ``runtime``."""
    from .engine import IdentityRefused, IdentityUnavailable

    terminal = io or CliIo()
    args = build_parser().parse_args(list(argv))
    try:
        return await _HANDLERS[args.command](runtime, args, terminal)
    except (
        CliRefused,
        IdentityRefused,
        IdentityUnavailable,
        PermissionError,
    ) as refused:
        terminal.emit({"error": str(refused)})
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point: the served runtime over this host's configuration."""
    from agent_utilities.core.config import load_config

    from .serving import served_identity_runtime

    load_config()
    return asyncio.run(
        run(served_identity_runtime(), sys.argv[1:] if argv is None else argv)
    )
