"""Auth modes: profile defaults and the ``none``-mode guard rails (IDM-07).

``none`` is not "auth off": its authenticator always answers the bootstrap
principal, which then travels the same token/claims/session/engine path as
any signed-in user. What makes it safe to offer at all is that nobody but the
operator can reach it:

* every GraphOS listener binds loopback, unless the operator types the exact
  acknowledgement (:data:`NONE_MODE_ACK`) — a typo keeps the refusal;
* production profiles refuse ``none`` outright without the same
  acknowledgement;
* every request's ``Host`` must name the loopback interface (or the one name
  configured for an acknowledged exposure), and every state-changing request
  must carry a same-origin ``Origin`` — otherwise any web page the operator
  visits could drive a loopback administrator through DNS rebinding or CSRF.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .browser import request_header

__all__ = [
    "AUTH_MODES",
    "NONE_MODE_ACK",
    "NONE_MODE_BANNER",
    "NoneModeExposureRefused",
    "NoneModeRequestGuard",
    "default_mode_for_profile",
    "is_loopback_host",
    "mode_banner",
    "refuse_unsafe_none_mode",
]

AUTH_MODES = ("none", "local", "external")

#: The exact acknowledgement (shared with the engine's own transition check).
NONE_MODE_ACK = "I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN"

#: The banner every surface shows while the install runs in ``none`` mode.
NONE_MODE_BANNER = (
    "Unauthenticated demo mode — anyone who can reach this address is an administrator"
)

#: Operator ruling 2026-09-24: ``none`` for the tiny profile, ``local`` for
#: every production profile (with a forced first administrator).
_PROFILE_DEFAULT_MODES: Mapping[str, str] = {
    "tiny": "none",
    "single-node-prod": "local",
    "enterprise": "local",
}
_PRODUCTION_PROFILES = frozenset({"single-node-prod", "enterprise"})
_LOOPBACK_NAMES = frozenset({"localhost"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class NoneModeExposureRefused(RuntimeError):
    """``none`` mode was asked to serve where it is not safe to."""


def default_mode_for_profile(profile: str | None) -> str:
    """The first-boot auth mode of a deployment profile."""
    return _PROFILE_DEFAULT_MODES.get(str(profile or "tiny").strip(), "local")


def is_loopback_host(host: str) -> bool:
    """Whether a bind or ``Host`` name is confined to this machine."""
    candidate = host.strip().lower().strip("[]")
    if candidate in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def refuse_unsafe_none_mode(
    *, mode: str, profile: str | None, bind_hosts: Iterable[str], ack: str | None
) -> None:
    """Refuse to serve ``none`` mode off loopback or on a production profile.

    Raises:
        NoneModeExposureRefused: the exact acknowledgement is absent and a
            listener is non-loopback or the profile is a production one.
    """
    if mode != "none" or ack == NONE_MODE_ACK:
        return
    exposed = sorted(host for host in bind_hosts if not is_loopback_host(host))
    if exposed:
        raise NoneModeExposureRefused(
            "auth mode 'none' refuses a non-loopback listener; set "
            "GRAPHOS_AUTH_NONE_EXPOSE to the exact acknowledgement to expose it"
        )
    if str(profile or "tiny").strip() in _PRODUCTION_PROFILES:
        raise NoneModeExposureRefused(
            f"auth mode 'none' is refused on the {profile!r} profile without "
            "the exact GRAPHOS_AUTH_NONE_EXPOSE acknowledgement"
        )


def _host_name(authority: str) -> str:
    return (urlsplit(f"//{authority}").hostname or "").lower()


@dataclass(frozen=True)
class NoneModeRequestGuard:
    """Host / Origin enforcement applied to every request in ``none`` mode.

    ``exposed_name`` is the single extra host name an acknowledged exposure
    may answer to (``GRAPHOS_AUTH_NONE_HOSTNAME``).
    """

    exposed_name: str | None = None

    def _host_allowed(self, name: str) -> bool:
        if is_loopback_host(name):
            return True
        return bool(self.exposed_name) and name == str(self.exposed_name).lower()

    def refusal(self, scope: Mapping[str, Any]) -> str | None:
        """Why this request is refused in ``none`` mode, or ``None``."""
        hosts = request_header(scope, b"host")
        if len(hosts) != 1 or not self._host_allowed(_host_name(hosts[0])):
            return "host_not_loopback"
        method = str(scope.get("method") or "GET").upper()
        if scope.get("type") == "http" and method in _SAFE_METHODS:
            return None
        origins = request_header(scope, b"origin")
        if not origins:
            # Browsers send Origin on every cross-origin state change, so its
            # absence means a non-browser client; a COOKIE-authenticated
            # request without one is refused by the CSRF check instead.
            return None
        if len(origins) != 1:
            return "origin_ambiguous"
        origin = urlsplit(origins[0])
        if (origin.hostname or "").lower() != _host_name(hosts[0]) or origin.port != (
            urlsplit(f"//{hosts[0]}").port
        ):
            return "origin_not_same_host"
        return None


def mode_banner(mode: str) -> str | None:
    """The banner text for ``mode`` (only ``none`` has one)."""
    return NONE_MODE_BANNER if mode == "none" else None

