"""Typed model for an LDAPS bind configuration (GRAPHOS-IDENTITY-R010).

Slice .1: the typed model, construction validation, and refusal tests only.
The directory bind, filter escaping, and nested-group sync are later slices.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from .engine import IdentityUnavailable


@dataclass(frozen=True, slots=True)
class LdapBindConfig:
    """An LDAPS bind target; refused unless the scheme is ``ldaps``."""

    host: str
    port: int
    bind_dn: str
    scheme: str = "ldaps"

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise IdentityUnavailable("LDAP bind requires a directory host")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not (0 < self.port <= 65535)
        ):
            raise IdentityUnavailable("LDAP bind requires a valid port")
        if not isinstance(self.bind_dn, str) or not self.bind_dn:
            raise IdentityUnavailable("LDAP bind requires a bind DN")
        if self.scheme != "ldaps":
            raise IdentityUnavailable(
                "LDAP bind must use ldaps, plaintext ldap is refused"
            )


def _normalize_dn(dn: str) -> str:
    """Case-fold a DN and strip whitespace around RDN separators and ``=``."""
    parts = []
    for rdn in dn.split(","):
        attr, sep, value = rdn.partition("=")
        parts.append(f"{attr.strip()}{sep}{value.strip()}".casefold())
    return ",".join(parts)


def map_groups_to_roles(
    group_dns: Collection[str],
    mapping: Mapping[str, Collection[str]],
) -> frozenset[str]:
    """Roles granted by a directory user's groups (GRAPHOS-IDENTITY-R010.2.1).

    ``mapping`` is group DN to roles; DNs compare case-insensitively. Fails
    closed: no matching group, or a matching group yielding no role, is refused.
    """
    wanted = {_normalize_dn(dn): roles for dn, roles in mapping.items()}
    roles: set[str] = set()
    for dn in group_dns:
        roles.update(wanted.get(_normalize_dn(dn), ()))
    if not roles:
        raise IdentityUnavailable("no directory group maps to a role")
    return frozenset(roles)


class DirectoryPort(Protocol):
    """Injected directory client; the real LDAP transport lives behind it."""

    def bind(self, config: LdapBindConfig) -> None:
        """Bind to the directory, raising on failure."""


_FILTER_ESCAPES = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}


def escape_filter_value(value: str) -> str:
    """Escape an assertion value for an LDAP search filter (RFC 4515)."""
    return "".join(_FILTER_ESCAPES.get(ch, ch) for ch in value)


def bind_directory(port: DirectoryPort | None, config: LdapBindConfig) -> None:
    """Bind through the injected port (GRAPHOS-IDENTITY-R010.2.2).

    Fails closed: a missing port or any bind failure is ``IdentityUnavailable``.
    """
    if port is None:
        raise IdentityUnavailable("no directory port is configured")
    try:
        port.bind(config)
    except IdentityUnavailable:
        raise
    except Exception as exc:
        raise IdentityUnavailable("directory bind failed") from exc


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    """One directory entry as reported by the port: groups and disabled flag."""

    username: str
    group_dns: tuple[str, ...] = ()
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Typed result of a directory sync (GRAPHOS-IDENTITY-R010.2.3.1)."""

    assignments: Mapping[str, frozenset[str]]
    deprovision: tuple[str, ...]
    unmapped: tuple[str, ...]


def plan_directory_sync(
    snapshot: Collection[DirectoryUser],
    mapping: Mapping[str, Collection[str]],
) -> SyncPlan:
    """Pure sync plan from a directory snapshot (GRAPHOS-IDENTITY-R010.2.3.1).

    Disabled users are deprovisioned; enabled users get the roles their groups
    map to, or are listed as unmapped when no group maps. Fails closed on an
    empty snapshot, which would otherwise look like every account vanishing.
    """
    if not snapshot:
        raise IdentityUnavailable("empty directory snapshot, sync refused")
    assignments: dict[str, frozenset[str]] = {}
    deprovision: list[str] = []
    unmapped: list[str] = []
    for user in snapshot:
        if user.disabled:
            deprovision.append(user.username)
            continue
        try:
            assignments[user.username] = map_groups_to_roles(user.group_dns, mapping)
        except IdentityUnavailable:
            unmapped.append(user.username)
    return SyncPlan(assignments, tuple(deprovision), tuple(unmapped))
