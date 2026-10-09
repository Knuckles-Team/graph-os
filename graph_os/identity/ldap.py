"""Typed model for an LDAPS bind configuration (GRAPHOS-IDENTITY-R010).

Slice .1: the typed model, construction validation, and refusal tests only.
The directory bind, filter escaping, and nested-group sync are later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

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
