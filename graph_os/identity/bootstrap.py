"""Typed model for the loopback bootstrap principal (GRAPHOS-IDENTITY-R004).

Slice .1: the typed model, construction validation, and refusal tests only.
The resolution path that binds this principal into the ordinary RBAC/scope
flow is a later slice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import IdentityUnavailable

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True, slots=True)
class LoopbackBootstrapPrincipal:
    """The unauthenticated-mode principal, refused outside a loopback bind.

    ``bind_host`` is the socket's actual bind address, not a client-supplied
    header. ``exposure_acknowledged`` must be explicitly set by configuration
    before a non-loopback bind is even considered by a later slice; this
    slice refuses construction outright when the bind is not loopback.
    """

    bind_host: str
    exposure_acknowledged: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.bind_host, str) or not self.bind_host:
            raise IdentityUnavailable("bootstrap principal requires a bind host")
        if self.bind_host not in _LOOPBACK_HOSTS and not self.exposure_acknowledged:
            raise IdentityUnavailable(
                "non-loopback bind requires an explicit exposure acknowledgment"
            )
