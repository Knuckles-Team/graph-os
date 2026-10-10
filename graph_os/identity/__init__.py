"""Strict identity producer primitives; serving authority remains injected."""

from .client_entry_points import (
    CLIENT_TOKEN_REQUIREMENTS,
    ClientEntryPoint,
    TokenRequirement,
    entry_points_without_verified_tokens,
)
from .engine import IdentityUnavailable, Resolution
from .mail_capability import (
    EmailAffordances,
    MailAdapterRefused,
    SmtpAdapterSettings,
    email_affordances,
)

__all__ = [
    "CLIENT_TOKEN_REQUIREMENTS",
    "ClientEntryPoint",
    "EmailAffordances",
    "IdentityUnavailable",
    "MailAdapterRefused",
    "Resolution",
    "SmtpAdapterSettings",
    "TokenRequirement",
    "email_affordances",
    "entry_points_without_verified_tokens",
]
