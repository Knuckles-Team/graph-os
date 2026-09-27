"""Optional identity e-mail: absent ⇒ every feature hidden and nothing sent;
SMTP and listmonk transports; header-injection and cleartext refusals."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from graph_os.identity.smtp import (
    IdentityNotifier,
    ListmonkMailer,
    ListmonkSettings,
    MailPurpose,
    MailUnavailable,
    SmtpMailer,
    SmtpSettings,
    notifier_from_settings,
)
from tests.identity.fakes import FakeSecrets

LINK = "https://graphos.example/auth/reset?token=abc"


def test_no_transport_hides_every_feature_and_sends_nothing() -> None:
    notifier = notifier_from_settings({}, FakeSecrets())
    assert notifier.features() == {
        "password_reset_email": False,
        "email_verification": False,
        "invite_email": False,
    }


async def test_no_transport_refuses_to_send() -> None:
    notifier = notifier_from_settings({}, FakeSecrets())
    with pytest.raises(MailUnavailable):
        await notifier.send(
            MailPurpose.PASSWORD_RESET,
            "a@example.org",
            username="a",
            link=LINK,
            ttl_s=1800,
        )


class FakeSmtp:
    instances: list[FakeSmtp] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.address = (host, port)
        self.events: list[str] = []
        self.sent: list[Any] = []
        FakeSmtp.instances.append(self)

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.events.append("quit")

    def starttls(self, context: Any) -> None:
        self.events.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.events.append(f"login:{user}")

    def send_message(self, message: Any) -> None:
        self.events.append("send")
        self.sent.append(message)


def _smtp_notifier() -> IdentityNotifier:
    settings = SmtpSettings(
        "smtp.example", 587, "starttls", "idm@example.org", "idm", "pw"
    )
    return IdentityNotifier(SmtpMailer(settings, smtp_factory=cast(Any, FakeSmtp)))


async def test_smtp_sends_after_starttls_and_login() -> None:
    FakeSmtp.instances.clear()
    notifier = _smtp_notifier()
    assert all(notifier.features().values())
    await notifier.send(
        MailPurpose.PASSWORD_RESET,
        "alice@example.org",
        username="alice",
        link=LINK,
        ttl_s=1800,
    )
    (smtp,) = FakeSmtp.instances
    assert smtp.events == ["starttls", "login:idm", "send", "quit"]
    message = smtp.sent[0]
    assert message["To"] == "alice@example.org"
    assert LINK in message.get_content() and "30 minutes" in message.get_content()


@pytest.mark.parametrize(
    ("to", "username", "link"),
    [
        ("alice@example.org\r\nBcc: evil@example.net", "alice", LINK),
        ("alice@example.org", "alice\nSubject: hi", LINK),
        ("not-an-address", "alice", LINK),
        ("alice@example.org", "alice", "http://graphos.example/reset"),
        ("alice@example.org", "alice", "javascript:alert(1)"),
    ],
)
async def test_injection_and_unsafe_links_are_refused(
    to: str, username: str, link: str
) -> None:
    FakeSmtp.instances.clear()
    notifier = _smtp_notifier()
    await notifier.send(
        MailPurpose.INVITE, "ok@example.org", username="ok", link=LINK, ttl_s=600
    )  # baseline twin
    with pytest.raises(MailUnavailable):
        await notifier.send(
            MailPurpose.INVITE, to, username=username, link=link, ttl_s=600
        )
    assert len([s for s in FakeSmtp.instances if s.sent]) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "smtp.example", "security": "none"},
        {"host": "127.0.0.1", "security": "none", "password": "pw"},
        {"host": "smtp.example", "security": "ssl3"},
        {"host": "smtp.example", "sender": "nobody"},
    ],
)
def test_unsafe_smtp_settings_are_refused(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "host": "smtp.example",
        "port": 587,
        "security": "starttls",
        "sender": "idm@example.org",
    }
    SmtpSettings(**base)  # baseline twin
    with pytest.raises(ValueError):
        SmtpSettings(**(base | kwargs))


def test_loopback_relay_may_be_cleartext() -> None:
    SmtpSettings(host="localhost", port=25, security="none", sender="idm@example.org")


def _listmonk(handler: Any, templates: dict[MailPurpose, int]) -> ListmonkMailer:
    settings = ListmonkSettings(
        "https://listmonk.example", "graph-os", "tok", templates
    )
    return ListmonkMailer(
        settings,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


async def test_listmonk_transactional_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": True})

    mailer = _listmonk(handler, {MailPurpose.PASSWORD_RESET: 3})
    notifier = IdentityNotifier(mailer, mailer.purposes())
    assert notifier.features() == {
        "password_reset_email": True,
        "email_verification": False,
        "invite_email": False,
    }
    await notifier.send(
        MailPurpose.PASSWORD_RESET,
        "alice@example.org",
        username="alice",
        link=LINK,
        ttl_s=1800,
    )
    (request,) = seen
    assert request.url.path == "/api/tx"
    payload = json.loads(request.content)
    assert (
        payload["template_id"] == 3
        and payload["subscriber_email"] == "alice@example.org"
    )
    assert payload["data"]["link"] == LINK
    with pytest.raises(MailUnavailable):
        await notifier.send(
            MailPurpose.INVITE,
            "alice@example.org",
            username="alice",
            link=LINK,
            ttl_s=60,
        )


async def test_listmonk_refusal_surfaces() -> None:
    mailer = _listmonk(lambda request: httpx.Response(500), {MailPurpose.INVITE: 5})
    with pytest.raises(MailUnavailable):
        await IdentityNotifier(mailer, mailer.purposes()).send(
            MailPurpose.INVITE, "a@example.org", username="a", link=LINK, ttl_s=60
        )


def test_settings_resolve_secrets_by_reference() -> None:
    secrets = FakeSecrets({"mail/listmonk": "tok"})
    notifier = notifier_from_settings(
        {
            "GRAPHOS_LISTMONK_URL": "http://127.0.0.1:9000",
            "GRAPHOS_LISTMONK_TOKEN_REF": "mail/listmonk",
            "GRAPHOS_LISTMONK_TEMPLATES": "password_reset=3, invite=5",
        },
        secrets,
    )
    assert (
        notifier.features()["invite_email"]
        and not notifier.features()["email_verification"]
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"GRAPHOS_LISTMONK_URL": "https://listmonk.example"},
        {
            "GRAPHOS_LISTMONK_URL": "http://listmonk.example",
            "GRAPHOS_LISTMONK_TOKEN_REF": "mail/listmonk",
        },
        {
            "GRAPHOS_SMTP_HOST": "smtp.example",
            "GRAPHOS_MAIL_FROM": "idm@example.org",
            "GRAPHOS_SMTP_PASSWORD_REF": "missing",
        },
        {
            "GRAPHOS_LISTMONK_URL": "https://l.example",
            "GRAPHOS_LISTMONK_TOKEN_REF": "mail/listmonk",
            "GRAPHOS_LISTMONK_TEMPLATES": "bogus=1",
        },
    ],
)
def test_half_configured_transport_fails_at_startup(settings: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        notifier_from_settings(settings, FakeSecrets({"mail/listmonk": "tok"}))
