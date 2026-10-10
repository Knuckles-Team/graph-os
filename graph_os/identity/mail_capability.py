"""Optional identity e-mail capability (GRAPHOS-IDENTITY-R016.1).

GraphOS optionally sends password-reset, verification, and invitation
e-mail through a configurable SMTP adapter. This module is the graph-os
side of that contract: a typed, refusing settings model plus the
capability flags that hide email-only affordances when no adapter is
configured, so a deployment without mail still has a working
administrator-issued reset and recovery-code path.

The SMTP/listmonk transport itself, and its wiring into the password
reset / verification / invitation send pipeline, is
GRAPHOS-IDENTITY-R016.2 -- pinned to the epistemic-graph >=2.28 engine
release, which this repository does not yet depend on (current pin is
``epistemic-graph>=2.27.0,<3.0.0`` in ``pyproject.toml``).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "EmailAffordances",
    "MailAdapterRefused",
    "SmtpAdapterSettings",
    "email_affordances",
]


class MailAdapterRefused(ValueError):
    """The supplied SMTP adapter settings are not usable."""


@dataclass(frozen=True)
class SmtpAdapterSettings:
    """A validated SMTP adapter configuration.

    Construction refuses a missing host, an out-of-range port, or a
    ``mail_from`` address that is not a plausible e-mail address, so an
    unusable adapter is never treated as "configured".
    """

    host: str
    port: int
    mail_from: str
    username: str | None = None

    def __post_init__(self) -> None:
        if not self.host or not self.host.strip():
            raise MailAdapterRefused("refusing an SMTP adapter with no host")
        if not (1 <= self.port <= 65535):
            raise MailAdapterRefused(
                f"refusing an SMTP adapter with an invalid port {self.port!r}"
            )
        local, _, domain = (self.mail_from or "").rpartition("@")
        if not local or not domain or "." not in domain or " " in self.mail_from:
            raise MailAdapterRefused(
                "refusing an SMTP adapter with no valid mail_from address"
            )


@dataclass(frozen=True)
class EmailAffordances:
    """Which identity e-mail affordances GraphOS should offer right now."""

    password_reset_by_email: bool
    email_verification: bool
    email_invitation: bool

    # These paths must stay available regardless of mail configuration
    # (GRAPHOS-IDENTITY-R016: "administrator reset and recovery-code
    # paths remain available").
    admin_issued_reset: bool = True
    recovery_code: bool = True


def email_affordances(settings: SmtpAdapterSettings | None) -> EmailAffordances:
    """Resolve which email-only UI affordances GraphOS should show.

    With no adapter configured, every email-only affordance is hidden;
    the administrator-issued reset and recovery-code paths remain
    available either way.
    """
    configured = settings is not None
    return EmailAffordances(
        password_reset_by_email=configured,
        email_verification=configured,
        email_invitation=configured,
    )
