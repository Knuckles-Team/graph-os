"""Optional identity e-mail: password reset, e-mail verification, invitations
(IDM-19; operator ruling: e-mail is optional).

Two transports, one :class:`Mailer` seam:

* :class:`SmtpMailer` -- any SMTP relay, over implicit TLS (465) or STARTTLS
  (587) with a validated certificate; a cleartext relay is accepted only on a
  loopback host (a local MTA);
* :class:`ListmonkMailer` -- listmonk's transactional API (``POST /api/tx``),
  the homelab's mail service, one template per purpose.

With neither configured the :class:`IdentityNotifier` has no mailer: every
e-mail feature reports ``False`` (:meth:`IdentityNotifier.features`) so no
route or page offers "forgot password" or "verify e-mail", and a send raises
:class:`MailUnavailable`. Recovery then is an admin-issued reset token or a
recovery code (design §3.3).

Configuration (``GRAPHOS_SMTP_*`` or ``GRAPHOS_LISTMONK_*``; secrets are
references resolved through the secrets backend, never values):

``GRAPHOS_SMTP_HOST`` ``GRAPHOS_SMTP_PORT`` ``GRAPHOS_SMTP_SECURITY``
(``tls``/``starttls``/``none``) ``GRAPHOS_SMTP_USERNAME``
``GRAPHOS_SMTP_PASSWORD_REF`` ``GRAPHOS_MAIL_FROM``;
``GRAPHOS_LISTMONK_URL`` ``GRAPHOS_LISTMONK_USER``
``GRAPHOS_LISTMONK_TOKEN_REF`` ``GRAPHOS_LISTMONK_TEMPLATES``
(``password_reset=3,email_verify=4,invite=5``).
"""

from __future__ import annotations

import ipaddress
import smtplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

import anyio
import httpx

from graph_os.identity.idp_common import SecretResolver

__all__ = [
    "IdentityNotifier",
    "ListmonkMailer",
    "ListmonkSettings",
    "MailPurpose",
    "MailUnavailable",
    "Mailer",
    "OutgoingMail",
    "SmtpMailer",
    "SmtpSettings",
    "notifier_from_settings",
]


class MailUnavailable(RuntimeError):
    """No mail transport is configured, or the transport refused the message."""


class MailPurpose(StrEnum):
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFY = "email_verify"
    INVITE = "invite"


_SUBJECTS = {
    MailPurpose.PASSWORD_RESET: "Reset your GraphOS password",
    MailPurpose.EMAIL_VERIFY: "Verify your GraphOS e-mail address",
    MailPurpose.INVITE: "You are invited to GraphOS",
}
_BODIES = {
    MailPurpose.PASSWORD_RESET: (
        "Someone asked to reset the password of the GraphOS account {username}.\n\n"
        "Open this link within {minutes} minutes to choose a new password:\n{link}\n\n"
        "If this was not you, ignore this message; the password is unchanged.\n"
    ),
    MailPurpose.EMAIL_VERIFY: (
        "Confirm that {address} belongs to the GraphOS account {username}:\n{link}\n\n"
        "The link expires in {minutes} minutes.\n"
    ),
    MailPurpose.INVITE: (
        "You have been invited to GraphOS as {username}.\n\n"
        "Open this link within {minutes} minutes to set your password:\n{link}\n"
    ),
}
_MAX_HEADER_CHARS = 320


def _header_safe(value: str, name: str) -> str:
    """Refuse header injection: no CR/LF/NUL, bounded length."""
    if not value or len(value) > _MAX_HEADER_CHARS or any(c in value for c in "\r\n\x00"):
        raise MailUnavailable(f"refusing an unsafe {name}")
    return value


def _address(value: str) -> str:
    local, at, domain = _header_safe(value, "address").rpartition("@")
    if not at or not local or "." not in domain or " " in value:
        raise MailUnavailable("refusing a malformed address")
    return value


def _https_link(link: str) -> str:
    parts = urlsplit(link)
    if parts.scheme != "https" or not parts.hostname:
        raise MailUnavailable("identity links must be https URLs")
    return link


@dataclass(frozen=True)
class OutgoingMail:
    """One rendered identity e-mail."""

    purpose: MailPurpose
    to: str
    subject: str
    text: str
    variables: Mapping[str, str]


class Mailer(Protocol):
    """A mail transport (blocking; the notifier runs it off the event loop)."""

    def send(self, mail: OutgoingMail) -> None: ...


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------
def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    sender: str
    username: str | None = None
    password: str | None = None
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.security not in ("tls", "starttls", "none"):
            raise ValueError("GRAPHOS_SMTP_SECURITY is tls, starttls or none")
        if self.security == "none" and not _is_loopback(self.host):
            raise ValueError("cleartext SMTP is refused except to a loopback relay")
        if self.security == "none" and self.password:
            raise ValueError("SMTP credentials are never sent in cleartext")
        try:
            _address(self.sender)
        except MailUnavailable:
            raise ValueError("GRAPHOS_MAIL_FROM must be one e-mail address") from None


SmtpFactory = Callable[..., smtplib.SMTP]


class SmtpMailer:
    """:class:`Mailer` over :mod:`smtplib`, certificate-validated TLS."""

    def __init__(self, settings: SmtpSettings, *, smtp_factory: SmtpFactory | None = None) -> None:
        self._settings = settings
        self._context = ssl.create_default_context()
        self._factory = smtp_factory

    def _connect(self) -> smtplib.SMTP:
        s = self._settings
        if self._factory is not None:
            return self._factory(s.host, s.port, timeout=s.timeout_s)
        if s.security == "tls":
            return smtplib.SMTP_SSL(s.host, s.port, timeout=s.timeout_s, context=self._context)
        return smtplib.SMTP(s.host, s.port, timeout=s.timeout_s)

    def _message(self, mail: OutgoingMail) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr(("GraphOS", self._settings.sender))
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message.set_content(mail.text)
        return message

    def send(self, mail: OutgoingMail) -> None:
        s = self._settings
        try:
            with self._connect() as client:
                if s.security == "starttls":
                    client.starttls(context=self._context)
                if s.username and s.password:
                    client.login(s.username, s.password)
                client.send_message(self._message(mail))
        except (smtplib.SMTPException, OSError) as exc:
            raise MailUnavailable(f"the SMTP relay refused the message ({type(exc).__name__})") from None


# ---------------------------------------------------------------------------
# listmonk
# ---------------------------------------------------------------------------
def _in_cluster(host: str) -> bool:
    return _is_loopback(host) or host.endswith((".svc", ".svc.cluster.local"))


@dataclass(frozen=True)
class ListmonkSettings:
    url: str
    user: str
    token: str
    templates: Mapping[MailPurpose, int]
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme == "https" and parts.hostname:
            return
        if parts.scheme == "http" and _in_cluster(parts.hostname or ""):
            return
        raise ValueError(
            "GRAPHOS_LISTMONK_URL must be https (http only to loopback or an in-cluster service)"
        )


class ListmonkMailer:
    """:class:`Mailer` over listmonk's transactional API (``POST /api/tx``).

    listmonk renders the purpose's template with the ``data`` variables
    (``link``, ``username``, ``minutes``, ``address``); a purpose without a
    configured template is not offered.
    """

    def __init__(
        self,
        settings: ListmonkSettings,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=settings.timeout_s))

    def purposes(self) -> frozenset[MailPurpose]:
        return frozenset(self._settings.templates)

    def send(self, mail: OutgoingMail) -> None:
        template = self._settings.templates.get(mail.purpose)
        if template is None:
            raise MailUnavailable(f"no listmonk template for {mail.purpose.value}")
        payload = {
            "subscriber_email": mail.to,
            "template_id": template,
            "data": dict(mail.variables),
            "content_type": "plain",
        }
        url = self._settings.url.rstrip("/") + "/api/tx"
        try:
            with self._client_factory() as client:
                response = client.post(url, json=payload, auth=(self._settings.user, self._settings.token))
        except httpx.HTTPError as exc:
            raise MailUnavailable(f"listmonk is unreachable ({type(exc).__name__})") from None
        if response.status_code >= 300:
            raise MailUnavailable(f"listmonk refused the message (HTTP {response.status_code})")


# ---------------------------------------------------------------------------
# The notifier
# ---------------------------------------------------------------------------
class IdentityNotifier:
    """Renders and sends identity e-mail; without a mailer it offers nothing."""

    def __init__(self, mailer: Mailer | None, purposes: frozenset[MailPurpose] | None = None) -> None:
        self._mailer = mailer
        self._purposes = frozenset(MailPurpose) if purposes is None else purposes
        if mailer is None:
            self._purposes = frozenset()

    def offers(self, purpose: MailPurpose) -> bool:
        return purpose in self._purposes

    def features(self) -> dict[str, bool]:
        """What the login and admin pages may offer (``False`` hides a feature)."""
        return {
            "password_reset_email": self.offers(MailPurpose.PASSWORD_RESET),
            "email_verification": self.offers(MailPurpose.EMAIL_VERIFY),
            "invite_email": self.offers(MailPurpose.INVITE),
        }

    @staticmethod
    def render(purpose: MailPurpose, to: str, *, username: str, link: str, ttl_s: int) -> OutgoingMail:
        variables = {
            "username": _header_safe(username, "username"),
            "link": _https_link(link),
            "minutes": str(max(1, ttl_s // 60)),
            "address": _address(to),
        }
        return OutgoingMail(
            purpose=purpose,
            to=variables["address"],
            subject=_SUBJECTS[purpose],
            text=_BODIES[purpose].format(**variables),
            variables=variables,
        )

    async def send(self, purpose: MailPurpose, to: str, *, username: str, link: str, ttl_s: int) -> None:
        """Send one identity e-mail; :class:`MailUnavailable` when not offered."""
        if self._mailer is None or not self.offers(purpose):
            raise MailUnavailable(f"{purpose.value} e-mail is not configured")
        mail = self.render(purpose, to, username=username, link=link, ttl_s=ttl_s)
        await anyio.to_thread.run_sync(self._mailer.send, mail)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _templates(spec: str) -> dict[MailPurpose, int]:
    templates: dict[MailPurpose, int] = {}
    for item in filter(None, (part.strip() for part in spec.split(","))):
        name, _, number = item.partition("=")
        try:
            templates[MailPurpose(name.strip())] = int(number)
        except ValueError:
            raise ValueError(f"bad GRAPHOS_LISTMONK_TEMPLATES entry {item!r}") from None
    return templates


def _secret(settings: Mapping[str, Any], secrets: SecretResolver, name: str) -> str | None:
    reference = settings.get(name)
    if not reference:
        return None
    value = secrets.get(str(reference))
    if not value:
        raise ValueError(f"{name} names a secret that is not provisioned")
    return value


def _smtp(settings: Mapping[str, Any], secrets: SecretResolver) -> SmtpMailer:
    security = str(settings.get("GRAPHOS_SMTP_SECURITY") or "starttls")
    default_port = {"tls": 465, "starttls": 587, "none": 25}.get(security, 587)
    return SmtpMailer(
        SmtpSettings(
            host=str(settings["GRAPHOS_SMTP_HOST"]),
            port=int(settings.get("GRAPHOS_SMTP_PORT") or default_port),
            security=security,
            sender=str(settings.get("GRAPHOS_MAIL_FROM") or ""),
            username=settings.get("GRAPHOS_SMTP_USERNAME") or None,
            password=_secret(settings, secrets, "GRAPHOS_SMTP_PASSWORD_REF"),
        )
    )


def _listmonk(settings: Mapping[str, Any], secrets: SecretResolver) -> ListmonkMailer:
    token = _secret(settings, secrets, "GRAPHOS_LISTMONK_TOKEN_REF")
    if not token:
        raise ValueError("GRAPHOS_LISTMONK_TOKEN_REF is required with GRAPHOS_LISTMONK_URL")
    return ListmonkMailer(
        ListmonkSettings(
            url=str(settings["GRAPHOS_LISTMONK_URL"]),
            user=str(settings.get("GRAPHOS_LISTMONK_USER") or "graph-os"),
            token=token,
            templates=_templates(str(settings.get("GRAPHOS_LISTMONK_TEMPLATES") or "")),
        )
    )


def notifier_from_settings(settings: Mapping[str, Any], secrets: SecretResolver) -> IdentityNotifier:
    """The deployment's notifier: listmonk, else SMTP, else none (features off).

    A half-configured transport raises :class:`ValueError` at startup rather
    than silently hiding the feature.
    """
    if settings.get("GRAPHOS_LISTMONK_URL"):
        listmonk = _listmonk(settings, secrets)
        return IdentityNotifier(listmonk, listmonk.purposes())
    if settings.get("GRAPHOS_SMTP_HOST"):
        return IdentityNotifier(_smtp(settings, secrets))
    return IdentityNotifier(None)
