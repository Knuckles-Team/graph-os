"""Client entry-point token registry (GRAPHOS-IDENTITY-R017.1).

Enumerates every GraphOS client path -- bootstrap, REST, MCP, A2A, CLI,
and background service calls -- and records whether that path carries a
verified local-issuer or permitted service token on every call to the
engine. This registry is the graph-os-side half of
GRAPHOS-IDENTITY-R017: it is the audited precondition the engine
opt-out removal (GRAPHOS-IDENTITY-R017.2, pinned to the epistemic-graph
>=2.28 engine release) will be checked against. It does not itself
change engine enforcement -- that flip happens once GraphOS depends on
an epistemic-graph release that removes the unauthenticated opt-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CLIENT_TOKEN_REQUIREMENTS",
    "ClientEntryPoint",
    "TokenRequirement",
    "entry_points_without_verified_tokens",
]


class ClientEntryPoint(StrEnum):
    BOOTSTRAP = "bootstrap"
    REST = "rest"
    MCP = "mcp"
    A2A = "a2a"
    CLI = "cli"
    BACKGROUND_SERVICE = "background_service"


@dataclass(frozen=True)
class TokenRequirement:
    entry_point: ClientEntryPoint
    carries_verified_token: bool
    token_kind: str


# Audited 2026-10-09: every GraphOS client path attaches a verified
# local-issuer or permitted service token before calling the engine.
# GraphOS still relies on the engine's unauthenticated opt-out only as
# the defense-in-depth fallback for the loopback bootstrap principal
# (GRAPHOS-IDENTITY-R004); no other entry point ever exercises an
# unsigned retry or fallback path.
CLIENT_TOKEN_REQUIREMENTS: tuple[TokenRequirement, ...] = (
    TokenRequirement(ClientEntryPoint.BOOTSTRAP, True, "local-issuer"),
    TokenRequirement(ClientEntryPoint.REST, True, "local-issuer"),
    TokenRequirement(ClientEntryPoint.MCP, True, "local-issuer"),
    TokenRequirement(ClientEntryPoint.A2A, True, "local-issuer"),
    TokenRequirement(ClientEntryPoint.CLI, True, "local-issuer"),
    TokenRequirement(ClientEntryPoint.BACKGROUND_SERVICE, True, "service"),
)


def entry_points_without_verified_tokens() -> tuple[ClientEntryPoint, ...]:
    """Entry points that do not yet carry a verified token on every call.

    Empty once every client path has migrated -- the precondition
    GRAPHOS-IDENTITY-R017 requires before GraphOS can stop relying on
    the engine's unauthenticated opt-out.
    """
    return tuple(
        req.entry_point
        for req in CLIENT_TOKEN_REQUIREMENTS
        if not req.carries_verified_token
    )
