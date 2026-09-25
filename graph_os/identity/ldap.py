"""LDAP and Active Directory as an external identity authority (IDM-13).

**Sign-in.** ``POST /auth/ldap/{idp_id}/login`` takes a username and password,
finds exactly one entry for the username with the IdP's service bind, and then
binds AS that entry with the candidate password. Only a successful bind proves
the password: GraphOS never reads or compares a directory password. The entry's
stable subject (AD ``objectGUID`` / ``entryUUID``) and its groups go to the
engine's ``external_login`` (:mod:`.idp_common`), which applies the IdP's
mapping rules like any other IdP.

**Transport.** ``ldaps://`` or ``ldap://`` with StartTLS; a plain ``ldap://``
IdP is refused at configuration time and certificates are always validated.

**Injection.** Every user-supplied value that reaches a filter is escaped per
RFC 4515 (:func:`escape_filter_value`); the only unescaped filter text is the
administrator's ``user_filter``, which must be one parenthesised filter.

**Groups.** ``member_of`` reads the entry's ``memberOf`` DNs; ``ad_nested``
asks AD for every group the entry is in transitively with the
``LDAP_MATCHING_RULE_IN_CHAIN`` rule (``1.2.840.113556.1.4.1941``). Both give
the claim paths ``groups`` (each group's first RDN value, e.g. ``Finance``)
and ``memberOf`` (full DNs).

**Disabled accounts.** An AD entry whose ``userAccountControl`` has bit 2 set
(``ACCOUNTDISABLE``) cannot sign in and is deprovisioned by the scheduled sync
(:mod:`.ldap_sync`).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anyio
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from graph_os.identity.idp_common import (
    NO_STORE,
    ExternalAssertion,
    IdpDirectory,
    IdpRecord,
    LoginCompleter,
    OneShotBackend,
    SecretResolver,
    UnknownIdp,
    truncate_ip,
)

__all__ = [
    "AD_DISABLED_BIT",
    "IN_CHAIN_RULE",
    "DirectoryEntry",
    "DirectoryUnavailable",
    "FailureThrottle",
    "Ldap3Directory",
    "LdapBroker",
    "LdapDirectory",
    "LdapSettings",
    "escape_filter_value",
    "ldap3_directory_factory",
    "ldap_settings",
    "nested_groups_filter",
    "user_lookup_filter",
]

IN_CHAIN_RULE = "1.2.840.113556.1.4.1941"
"""AD ``LDAP_MATCHING_RULE_IN_CHAIN``: transitive (nested) group membership."""
AD_DISABLED_BIT = 0x2
"""AD ``userAccountControl`` ``ACCOUNTDISABLE``."""

_MAX_USERNAME_CHARS = 256
_MAX_PASSWORD_CHARS = 1024
_AD_ATTRIBUTES = {
    "username_attribute": "sAMAccountName",
    "subject_attribute": "objectGUID",
    "group_mode": "ad_nested",
}


class DirectoryUnavailable(RuntimeError):
    """The directory could not be reached or answered out of shape."""


# ---------------------------------------------------------------------------
# RFC 4515 escaping
# ---------------------------------------------------------------------------
_FILTER_SPECIALS = frozenset("\\*()\x00")


def _escape_char(char: str) -> str:
    if char in _FILTER_SPECIALS or not (0x20 <= ord(char) < 0x7F):
        return "".join(f"\\{byte:02x}" for byte in char.encode("utf-8"))
    return char


def escape_filter_value(value: str) -> str:
    """``value`` as an RFC 4515 assertion value: ``\\ * ( )`` and NUL become
    ``\\XX``, and so does every byte outside printable ASCII, so no input can
    close the filter, add a component or become a wildcard."""
    return "".join(_escape_char(char) for char in value)


def _balanced_filter(text: str) -> bool:
    depth = 0
    for index, char in enumerate(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0 or (depth == 0 and index != len(text) - 1):
            return False
    return depth == 0 and text.startswith("(")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class LdapSettings(BaseModel):
    """``IdpConfig.config_json`` of a ``kind=ldap`` IdP. The service bind
    password is the IdP's ``secret_ref``, never a value here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    start_tls: bool = False
    ca_cert_file: str | None = None
    bind_dn: str = Field(min_length=1, max_length=1024)
    user_base_dn: str = Field(min_length=1, max_length=1024)
    user_filter: str = "(objectClass=person)"
    directory: Literal["generic", "active_directory"] = "generic"
    username_attribute: str = "uid"
    subject_attribute: str = "entryUUID"
    email_attribute: str = "mail"
    display_name_attribute: str = "displayName"
    group_mode: Literal["member_of", "ad_nested", "none"] = "member_of"
    group_base_dn: str | None = None
    timeout_s: int = Field(default=10, ge=1, le=60)
    sync_interval_s: int = Field(default=900, ge=60, le=86_400)
    max_deprovision_ratio: float = Field(default=0.25, ge=0.0, le=1.0)
    """A sync that would deprovision more than this share of the IdP's
    provisioned principals deprovisions none (a wrong base DN or an empty
    search must not lock everyone out)."""

    @model_validator(mode="before")
    @classmethod
    def _ad_defaults(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and data.get("directory") == "active_directory":
            return {**_AD_ATTRIBUTES, **data}
        return data

    @field_validator("url")
    @classmethod
    def _tls_url(cls, value: str) -> str:
        scheme, _, rest = value.partition("://")
        if scheme not in ("ldaps", "ldap") or not rest.strip("/"):
            raise ValueError("an LDAP IdP URL is ldaps://host[:port] or ldap://host[:port]")
        return value

    @field_validator("user_filter")
    @classmethod
    def _one_filter(cls, value: str) -> str:
        if not _balanced_filter(value):
            raise ValueError("user_filter must be one parenthesised LDAP filter")
        return value

    @model_validator(mode="after")
    def _encrypted(self) -> LdapSettings:
        if self.url.startswith("ldap://") and not self.start_tls:
            raise ValueError("plain LDAP is refused: use ldaps:// or ldap:// with start_tls")
        if self.group_mode == "ad_nested" and not self.group_base_dn:
            raise ValueError("ad_nested group resolution needs group_base_dn")
        return self


def user_lookup_filter(settings: LdapSettings, username: str) -> str:
    """The filter that finds one user by name; the name is always escaped."""
    return f"(&{settings.user_filter}({settings.username_attribute}={escape_filter_value(username)}))"


def nested_groups_filter(user_dn: str) -> str:
    """AD: every group ``user_dn`` is a member of, transitively."""
    return f"(&(objectClass=group)(member:{IN_CHAIN_RULE}:={escape_filter_value(user_dn)}))"


# ---------------------------------------------------------------------------
# Directory port
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DirectoryEntry:
    """One directory user, reduced to what the engine reads."""

    dn: str
    subject: str
    username: str
    email: str | None = None
    display_name: str | None = None
    active: bool = True
    groups: tuple[str, ...] = ()
    member_of: tuple[str, ...] = ()

    def claims(self) -> dict[str, list[str]]:
        """The claim paths the IdP's mapping rules read."""
        claims = {"groups": list(self.groups), "memberOf": list(self.member_of)}
        if self.email:
            claims["email"] = [self.email]
        return {path: values for path, values in claims.items() if values}


class LdapDirectory(Protocol):
    """The directory operations the broker and the sync need (blocking)."""

    def find_user(self, username: str) -> DirectoryEntry | None:
        """The one entry for ``username`` (``None`` if absent or ambiguous)."""
        ...

    def verify_password(self, dn: str, password: str) -> bool:
        """Whether a simple bind as ``dn`` with ``password`` succeeds."""
        ...

    def users(self) -> Iterator[DirectoryEntry]:
        """Every user under the base DN matching the user filter."""
        ...


def _first(attributes: Mapping[str, Any], name: str) -> Any:
    value = attributes.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bytes):
        return None
    text = str(value).strip()
    return text or None


def _subject(raw: Any) -> str | None:
    """``objectGUID`` (16 bytes, little-endian GUID) or any textual id."""
    if isinstance(raw, bytes) and len(raw) == 16:
        return str(uuid.UUID(bytes_le=raw))
    return _text(raw)


def _rdn_value(dn: str) -> str | None:
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.dn import parse_dn

    try:
        parts = parse_dn(dn)
    except LDAPException:
        return None
    return str(parts[0][1]) if parts else None


def _ad_active(attributes: Mapping[str, Any]) -> bool:
    control = _first(attributes, "userAccountControl")
    try:
        return not int(control or 0) & AD_DISABLED_BIT
    except (TypeError, ValueError):
        return False


ConnectionFactory = Callable[[str, str], Any]
"""``(bind_dn, password) -> an unbound ldap3 Connection``."""


class Ldap3Directory:
    """:class:`LdapDirectory` over ``ldap3`` (the ``graph-os[ldap]`` extra)."""

    def __init__(
        self,
        settings: LdapSettings,
        bind_password: str,
        *,
        connection_factory: ConnectionFactory | None = None,
        page_size: int = 500,
    ) -> None:
        self._settings = settings
        self._bind_password = bind_password
        self._connect = connection_factory or self._tls_connection
        self._page_size = page_size

    def _tls_connection(self, user: str, password: str) -> Any:
        import ssl

        from ldap3 import Connection, Server, Tls

        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=self._settings.ca_cert_file)
        server = Server(
            self._settings.url,
            use_ssl=self._settings.url.startswith("ldaps://"),
            tls=tls,
            connect_timeout=self._settings.timeout_s,
        )
        return Connection(
            server,
            user=user,
            password=password,
            read_only=True,
            receive_timeout=self._settings.timeout_s,
        )

    def _bound(self, user: str, password: str) -> Any | None:
        from ldap3.core.exceptions import LDAPException

        connection = self._connect(user, password)
        try:
            if self._settings.start_tls:
                connection.open()
                connection.start_tls()
            return connection if connection.bind() else None
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailable(type(exc).__name__) from None

    def _service(self) -> Any:
        connection = self._bound(self._settings.bind_dn, self._bind_password)
        if connection is None:
            raise DirectoryUnavailable("the service bind was refused")
        return connection

    def _attributes(self) -> list[str]:
        s = self._settings
        names = [s.username_attribute, s.subject_attribute, s.email_attribute]
        names.append(s.display_name_attribute)
        names.extend(("memberOf", "userAccountControl") if s.directory == "active_directory" else ("memberOf",))
        return names

    def _groups(self, connection: Any, dn: str, attributes: Mapping[str, Any]) -> tuple[list[str], list[str]]:
        mode = self._settings.group_mode
        if mode == "none":
            return [], []
        if mode == "member_of":
            dns = [str(v) for v in attributes.get("memberOf") or () if isinstance(v, str)]
        else:
            dns = self._nested_group_dns(connection, dn)
        names = [name for name in (_rdn_value(d) for d in dns) if name]
        return sorted(set(names)), sorted(set(dns))

    def _nested_group_dns(self, connection: Any, dn: str) -> list[str]:
        from ldap3 import SUBTREE

        base = self._settings.group_base_dn or self._settings.user_base_dn
        connection.search(base, nested_groups_filter(dn), SUBTREE, attributes=["cn"])
        return [str(r["dn"]) for r in connection.response or () if r.get("type") == "searchResEntry"]

    def _read_subject(self, raw: Mapping[str, Any]) -> str | None:
        """``objectGUID`` is read from the raw bytes; any other id as text."""
        name = self._settings.subject_attribute
        source = raw.get("raw_attributes" if name == "objectGUID" else "attributes") or {}
        return _subject(_first(source, name))

    def _entry(self, connection: Any, raw: Mapping[str, Any]) -> DirectoryEntry | None:
        s = self._settings
        attributes = raw.get("attributes") or {}
        subject = self._read_subject(raw)
        username = _text(_first(attributes, s.username_attribute))
        if subject is None or username is None:
            return None
        groups, member_of = self._groups(connection, str(raw["dn"]), attributes)
        return DirectoryEntry(
            dn=str(raw["dn"]),
            subject=subject,
            username=username,
            email=_text(_first(attributes, s.email_attribute)),
            display_name=_text(_first(attributes, s.display_name_attribute)),
            active=_ad_active(attributes) if s.directory == "active_directory" else True,
            groups=tuple(groups),
            member_of=tuple(member_of),
        )

    def find_user(self, username: str) -> DirectoryEntry | None:
        from ldap3 import SUBTREE

        connection = self._service()
        try:
            connection.search(
                self._settings.user_base_dn,
                user_lookup_filter(self._settings, username),
                SUBTREE,
                attributes=self._attributes(),
                size_limit=2,
            )
            hits = [r for r in connection.response or () if r.get("type") == "searchResEntry"]
            return self._entry(connection, hits[0]) if len(hits) == 1 else None
        finally:
            connection.unbind()

    def verify_password(self, dn: str, password: str) -> bool:
        if not password:
            return False
        connection = self._bound(dn, password)
        if connection is None:
            return False
        connection.unbind()
        return True

    def users(self) -> Iterator[DirectoryEntry]:
        from ldap3 import SUBTREE

        connection = self._service()
        try:
            pages = connection.extend.standard.paged_search(
                self._settings.user_base_dn,
                self._settings.user_filter,
                SUBTREE,
                attributes=self._attributes(),
                paged_size=self._page_size,
                generator=True,
            )
            for raw in pages:
                entry = self._entry(connection, raw) if raw.get("type") == "searchResEntry" else None
                if entry is not None:
                    yield entry
        finally:
            connection.unbind()


def ldap_settings(record: IdpRecord) -> LdapSettings:
    """The record's LDAP settings, or :class:`UnknownIdp` when they are invalid."""
    try:
        return LdapSettings.model_validate(record.config)
    except ValidationError:
        raise UnknownIdp(record.idp_id) from None


DirectoryFactory = Callable[[IdpRecord], LdapDirectory]


def ldap3_directory_factory(secrets: SecretResolver) -> DirectoryFactory:
    """Build an :class:`Ldap3Directory` per IdP from its settings and bind secret."""

    def build(record: IdpRecord) -> LdapDirectory:
        settings = ldap_settings(record)
        password = secrets.get(record.secret_ref) if record.secret_ref else None
        if not password:
            raise DirectoryUnavailable("the LDAP service bind secret is not provisioned")
        return Ldap3Directory(settings, password)

    return build


# ---------------------------------------------------------------------------
# Failure throttle (a failed bind never reaches the engine's throttle)
# ---------------------------------------------------------------------------
class FailureThrottle:
    """Exponential back-off per key over the shared secrets backend, so every
    replica sees the same counters: after ``free`` failures each further one
    doubles the wait, capped at ``cap_s``; a success clears the key."""

    def __init__(
        self,
        backend: OneShotBackend,
        *,
        free: int = 5,
        cap_s: float = 900.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._free = free
        self._cap_s = cap_s
        self._clock = clock

    @staticmethod
    def _key(key: str) -> str:
        return f"ldap-throttle:{key}"

    def _read(self, key: str) -> tuple[str | None, int, float]:
        raw = self._backend.get(self._key(key))
        if raw is None:
            return None, 0, 0.0
        record = json.loads(raw)
        return raw, int(record["failures"]), float(record["until"])

    def blocked(self, key: str) -> bool:
        _, _, until = self._read(key)
        return until > self._clock()

    def failure(self, key: str) -> None:
        for _ in range(3):
            raw, failures, _ = self._read(key)
            failures += 1
            wait = min(self._cap_s, 2.0 ** (failures - self._free)) if failures > self._free else 0.0
            record = json.dumps({"failures": failures, "until": self._clock() + wait})
            if raw is None and self._backend.set_if_absent(self._key(key), record):
                return
            if raw is not None and self._backend.compare_and_set(self._key(key), raw, record):
                return

    def success(self, key: str) -> None:
        self._backend.delete(self._key(key))


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------
def _login_error(code: str) -> Response:
    return RedirectResponse(f"/auth/login?error={code}", status_code=303, headers=NO_STORE)


def _credentials(form: Mapping[str, Any]) -> tuple[str, str] | None:
    username, password = form.get("username"), form.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    username = username.strip()
    if not username or len(username) > _MAX_USERNAME_CHARS:
        return None
    if not password or len(password) > _MAX_PASSWORD_CHARS:
        return None
    return username, password


class LdapBroker:
    """Sign-in with every enabled LDAP IdP; the engine decides who you are."""

    def __init__(
        self,
        *,
        directory: IdpDirectory,
        directories: DirectoryFactory,
        completer: LoginCompleter,
        throttle: FailureThrottle,
    ) -> None:
        self._directory = directory
        self._directories = directories
        self._completer = completer
        self._throttle = throttle

    def _verify(self, record: IdpRecord, username: str, password: str) -> DirectoryEntry | None:
        directory = self._directories(record)
        entry = directory.find_user(username)
        if entry is None or not entry.active:
            return None
        return entry if directory.verify_password(entry.dn, password) else None

    def _throttle_keys(self, record: IdpRecord, username: str, request: Request) -> tuple[str, ...]:
        account = f"acct:{record.idp_id}:{username.casefold()}"
        network = truncate_ip(request.client.host if request.client else None)
        return (account, f"ip:{network}") if network else (account,)

    async def login(self, request: Request) -> Response:
        """``POST /auth/ldap/{idp_id}/login`` (form ``username``, ``password``)."""
        credentials = _credentials(await request.form())
        if credentials is None:
            return _login_error("denied")
        try:
            record = await self._directory.enabled(request.path_params["idp_id"], "ldap")
        except UnknownIdp:
            return _login_error("idp_unavailable")
        keys = self._throttle_keys(record, credentials[0], request)
        if any(self._throttle.blocked(key) for key in keys):
            return _login_error("throttled")
        try:
            entry = await anyio.to_thread.run_sync(self._verify, record, *credentials)
        except (UnknownIdp, DirectoryUnavailable):
            return _login_error("idp_unavailable")
        if entry is None:
            for key in keys:
                self._throttle.failure(key)
            return _login_error("denied")
        self._throttle.success(keys[0])
        assertion = ExternalAssertion(
            idp_id=record.idp_id,
            subject=entry.subject,
            claims=entry.claims(),
            username_hint=entry.username,
        )
        return await self._completer.complete(request, assertion)

    def routes(self) -> list[Route]:
        return [Route("/auth/ldap/{idp_id}/login", self.login, methods=["POST"])]
