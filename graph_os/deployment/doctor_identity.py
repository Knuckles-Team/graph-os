"""Doctor check ``identity_mode``: the auth mode's exposure and the secrets it
relies on (IDENTITY-AND-AUTH-MODES-DESIGN §2.4, §2.5, §5.3; IDM-16).

The check reads the deployment's settings only (the stored mode is the
engine's; at runtime the stored mode wins and ``prepare_identity`` enforces
the same rules). It is red when:

* ``none`` would serve off loopback or on a production profile — even with
  the acknowledgement, an exposed ``none`` install is unsecured;
* a production profile has no issuer URL / tenant for the local issuer;
* a production profile runs on an auto-generated ``GRAPH_SERVICE_AUTH_SECRET``
  (rotate it on the first move out of ``none``; pgwire passwords derive from
  it).

A loopback ``none`` demo is a warning, as is an auto-generated secret on the
tiny profile.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from graph_os.identity.modes import (
    NONE_MODE_ACK,
    default_mode_for_profile,
    is_loopback_host,
)

from .doctor_support import _result

__all__ = ["IdentitySettings", "check_identity_mode", "evaluate_identity"]

_NAME = "identity_mode"
_PRODUCTION = frozenset({"single-node-prod", "enterprise"})
_CLAIM = "graph-os-identity claim"


@dataclass(frozen=True)
class IdentitySettings:
    """The deployment inputs the check reads."""

    profile: str
    mode: str
    bind_host: str
    none_ack: str | None
    issuer: str | None
    tenant: str | None
    auth_secret_configured: bool

    @classmethod
    def from_settings(
        cls, config: Any, setting: Callable[[str, Any], Any]
    ) -> IdentitySettings:
        profile = str(getattr(config, "deployment_profile", None) or "tiny")

        def text(name: str) -> str | None:
            return str(setting(name, "") or "").strip() or None

        return cls(
            profile=profile,
            mode=text("GRAPHOS_AUTH_MODE") or default_mode_for_profile(profile),
            bind_host=str(getattr(config, "host", None) or "127.0.0.1"),
            none_ack=text("GRAPHOS_AUTH_NONE_EXPOSE"),
            issuer=text("GRAPHOS_IDENTITY_ISSUER"),
            tenant=text("GRAPHOS_IDENTITY_TENANT"),
            auth_secret_configured=bool(
                str(getattr(config, "graph_service_auth_secret", "") or "").strip()
            ),
        )

    @property
    def production(self) -> bool:
        return self.profile in _PRODUCTION


def _none_mode(settings: IdentitySettings) -> dict[str, Any]:
    exposed = settings.production or not is_loopback_host(settings.bind_host)
    if exposed:
        acknowledged = settings.none_ack == NONE_MODE_ACK
        detail = (
            "auth mode 'none' is exposed"
            + (" (acknowledged)" if acknowledged else " and will refuse to start")
            + ": anyone who can reach this address is an administrator"
        )
        return _result(
            _NAME, "fail", detail, remediation=f"Run `{_CLAIM}` and move to local mode."
        )
    return _result(
        _NAME,
        "warn",
        "unsecured demo mode on loopback: the bootstrap administrator is implicit",
        remediation=f"Secure this install with `{_CLAIM}` before exposing it.",
    )


def _secured_mode(settings: IdentitySettings) -> dict[str, Any]:
    if settings.production and not (settings.issuer and settings.tenant):
        return _result(
            _NAME,
            "fail",
            "the local issuer has no issuer URL / tenant on a production profile",
            remediation="Set GRAPHOS_IDENTITY_ISSUER and GRAPHOS_IDENTITY_TENANT.",
        )
    if not settings.auth_secret_configured:
        status = "fail" if settings.production else "warn"
        return _result(
            _NAME,
            status,
            "GRAPH_SERVICE_AUTH_SECRET is auto-generated per install",
            remediation=(
                "Set GRAPH_SERVICE_AUTH_SECRET from the secrets backend and restart "
                "the engine and graph-os (pgwire passwords derive from it)."
            ),
        )
    return _result(
        _NAME, "ok", f"auth mode {settings.mode!r}; issuer and secrets configured"
    )


def evaluate_identity(settings: IdentitySettings) -> dict[str, Any]:
    """The check's verdict for ``settings``."""
    if settings.mode == "none":
        return _none_mode(settings)
    return _secured_mode(settings)


def check_identity_mode() -> dict[str, Any]:
    from agent_utilities.core.config import config, setting

    return evaluate_identity(IdentitySettings.from_settings(config, setting))
