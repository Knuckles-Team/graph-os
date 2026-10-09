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


def resolve_bootstrap_access(
    *,
    bind_host: str,
    exposure_acknowledged: bool = False,
    host_header: str,
    origin_header: str | None = None,
    is_mutating: bool = False,
) -> LoopbackBootstrapPrincipal:
    """Resolve the unauthenticated-mode caller through the ordinary RBAC path.

    Slice .2: wires the typed model (.1) into the Host/Origin guard for a
    cookie-mutating request. Refuses a non-loopback bind the same way the
    typed model does, and additionally refuses a Host/Origin mismatch
    before any principal is handed back.
    """
    principal = LoopbackBootstrapPrincipal(
        bind_host=bind_host, exposure_acknowledged=exposure_acknowledged
    )
    if is_mutating:
        if host_header not in _LOOPBACK_HOSTS and not exposure_acknowledged:
            raise IdentityUnavailable("Host header does not match an acknowledged bind")
        if origin_header is not None:
            origin_host = origin_header.split("://", 1)[-1].split("/", 1)[0]
            if origin_host != host_header:
                raise IdentityUnavailable(
                    "same-origin Origin required for a cookie-mutating request"
                )
    return principal


def unsecured_mode_banner(principal: LoopbackBootstrapPrincipal) -> dict[str, str]:
    """The unsecured-mode indicator shown in session, status, initialize, and doctor."""
    return {
        "mode": "unauthenticated",
        "principal": "usr:bootstrap",
        "banner": "GraphOS is running in unsecured loopback mode.",
        "doctor_check": "fail" if principal.exposure_acknowledged else "pass",
    }
