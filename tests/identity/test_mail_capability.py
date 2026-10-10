"""Tests for the optional SMTP identity-mail capability (GRAPHOS-IDENTITY-R016)."""

from __future__ import annotations

import pytest

from graph_os.identity.mail_capability import (
    EmailAffordances,
    MailAdapterRefused,
    SmtpAdapterSettings,
    email_affordances,
)


@pytest.mark.spec("GRAPHOS-IDENTITY-R016")
def test_email_affordances_hidden_without_adapter() -> None:
    affordances = email_affordances(None)

    assert affordances == EmailAffordances(
        password_reset_by_email=False,
        email_verification=False,
        email_invitation=False,
    )
    # The admin-issued reset and recovery-code paths remain available
    # even when no mail adapter is configured.
    assert affordances.admin_issued_reset is True
    assert affordances.recovery_code is True


@pytest.mark.spec("GRAPHOS-IDENTITY-R016")
def test_email_affordances_shown_once_an_adapter_is_configured() -> None:
    settings = SmtpAdapterSettings(
        host="smtp.example.org", port=587, mail_from="noreply@example.org"
    )

    affordances = email_affordances(settings)

    assert affordances.password_reset_by_email is True
    assert affordances.email_verification is True
    assert affordances.email_invitation is True
    assert affordances.admin_issued_reset is True
    assert affordances.recovery_code is True


@pytest.mark.spec("GRAPHOS-IDENTITY-R016")
@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "", "port": 587, "mail_from": "a@b.com"},
        {"host": "   ", "port": 587, "mail_from": "a@b.com"},
        {"host": "smtp.example.org", "port": 0, "mail_from": "a@b.com"},
        {"host": "smtp.example.org", "port": 70000, "mail_from": "a@b.com"},
        {"host": "smtp.example.org", "port": 587, "mail_from": "not-an-email"},
        {"host": "smtp.example.org", "port": 587, "mail_from": ""},
    ],
)
def test_smtp_adapter_settings_refuses_unusable_config(kwargs: dict) -> None:
    with pytest.raises(MailAdapterRefused):
        SmtpAdapterSettings(**kwargs)
