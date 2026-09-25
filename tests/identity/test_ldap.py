"""LDAP / AD: filter-injection corpus, TLS-only settings, bind sign-in through
the served route, and the scheduled group sync -- each refusal paired with the
accepted baseline built by the same helper.

The directory is ldap3's in-memory ``MOCK_SYNC`` server, so the real
:class:`Ldap3Directory` code (filters, binds, paging, attribute decoding)
runs; AD's in-chain matching rule is not implemented by the mock and is
covered at the filter level.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, OFFLINE_SLAPD_2_4, Connection, Server
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.identity.idp_common import SESSION_COOKIE, EngineLoginCompleter, IdpDirectory
from graph_os.identity.ldap import (
    IN_CHAIN_RULE,
    DirectoryEntry,
    DirectoryUnavailable,
    FailureThrottle,
    Ldap3Directory,
    LdapBroker,
    LdapSettings,
    escape_filter_value,
    nested_groups_filter,
    user_lookup_filter,
)
from graph_os.identity.ldap_sync import LdapSync
from tests.identity.fakes import FakeIdentityPort, FakeSecrets, idp_wire

BASE = "ou=people,dc=ex"
SERVICE_DN = "cn=svc,dc=ex"
GENERIC = {"url": "ldaps://ldap.example", "bind_dn": SERVICE_DN, "user_base_dn": BASE}
AD_BASE = "OU=People,DC=corp,DC=ex"
AD = {
    "url": "ldap://dc.corp.example",
    "start_tls": True,
    "directory": "active_directory",
    "bind_dn": "CN=svc,DC=corp,DC=ex",
    "user_base_dn": AD_BASE,
    "group_mode": "member_of",
}


STARTTLS_CALLS: list[str] = []


def _mock_directory(settings: dict[str, Any], info: Any, entries: dict[str, dict[str, Any]]) -> Ldap3Directory:
    """The real adapter over ldap3's in-memory server. The mock speaks no TLS,
    so StartTLS is recorded (it must precede every bind) instead of run."""
    server = Server("mock", get_info=info)
    seed = Connection(server, user=settings["bind_dn"], password="svc-pw", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(settings["bind_dn"], {"userPassword": "svc-pw", "objectClass": "person"})
    for dn, attributes in entries.items():
        seed.strategy.add_entry(dn, attributes)

    def connect(user: str, password: str) -> Connection:
        connection = Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)
        connection.strategy.entries = seed.strategy.entries
        connection.start_tls = lambda: STARTTLS_CALLS.append(user) or True
        return connection

    return Ldap3Directory(LdapSettings.model_validate(settings), "svc-pw", connection_factory=connect)


def _person(uid: str, password: str, *, groups: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "objectClass": "person",
        "uid": uid,
        "userPassword": password,
        "entryUUID": str(uuid.uuid5(uuid.NAMESPACE_DNS, uid)),
        "mail": f"{uid}@example.org",
        "memberOf": [f"cn={group},ou=groups,dc=ex" for group in groups],
    }


@pytest.fixture
def generic() -> Ldap3Directory:
    return _mock_directory(
        GENERIC,
        OFFLINE_SLAPD_2_4,
        {
            f"uid=alice,{BASE}": _person("alice", "alice-pw", groups=("Finance", "admins")),
            f"uid=bob,{BASE}": _person("bob", "bob-pw"),
            f"uid=a*,{BASE}": _person("a*", "star-pw"),
        },
    )


# ---------------------------------------------------------------------------
# RFC 4515 escaping + injection corpus
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("alice", "alice"),
        ("*", "\\2a"),
        ("a*", "a\\2a"),
        ("alice)(uid=*", "alice\\29\\28uid=\\2a"),
        ("*)(|(objectClass=*", "\\2a\\29\\28|\\28objectClass=\\2a"),
        ("back\\slash", "back\\5cslash"),
        ("nul\x00byte", "nul\\00byte"),
        ("josé", "jos\\c3\\a9"),
    ],
)
def test_filter_values_are_escaped(raw: str, escaped: str) -> None:
    assert escape_filter_value(raw) == escaped


@pytest.mark.parametrize(
    "probe", ["*", "a*", "alice)(uid=*", "*)(|(uid=*", "alice)(|(uid=bob", "ALICE*", "\\2a"]
)
def test_injection_corpus_finds_nobody(generic: Ldap3Directory, probe: str) -> None:
    assert generic.find_user("alice") is not None  # the baseline twin
    found = generic.find_user(probe)
    assert found is None or found.username == probe


def test_a_literal_star_username_is_matched_literally(generic: Ldap3Directory) -> None:
    entry = generic.find_user("a*")
    assert entry is not None and entry.username == "a*"


def test_lookup_filter_wraps_the_admin_filter() -> None:
    settings = LdapSettings.model_validate(GENERIC)
    assert user_lookup_filter(settings, "x)(y") == "(&(objectClass=person)(uid=x\\29\\28y))"


def test_nested_group_filter_uses_in_chain_and_escapes_the_dn() -> None:
    text = nested_groups_filter("CN=Smith\\, J (IT),OU=People,DC=corp,DC=ex")
    assert f"member:{IN_CHAIN_RULE}:=" in text
    assert "(IT)" not in text and "\\28IT\\29" in text


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "ldap://ldap.example"},
        {"url": "http://ldap.example"},
        {"user_filter": "objectClass=person"},
        {"user_filter": "(objectClass=person))(uid=*"},
        {"group_mode": "ad_nested"},
        {"extra": "nope"},
    ],
)
def test_unsafe_settings_are_refused(overrides: dict[str, Any]) -> None:
    LdapSettings.model_validate(GENERIC)  # the baseline twin
    with pytest.raises(ValueError):
        LdapSettings.model_validate(GENERIC | overrides)


def test_starttls_and_ad_defaults_are_accepted() -> None:
    settings = LdapSettings.model_validate(AD | {"group_mode": "ad_nested", "group_base_dn": "OU=Groups,DC=corp,DC=ex"})
    assert settings.username_attribute == "sAMAccountName"
    assert settings.subject_attribute == "objectGUID"


# ---------------------------------------------------------------------------
# The directory adapter
# ---------------------------------------------------------------------------
def test_entry_carries_subject_groups_and_email(generic: Ldap3Directory) -> None:
    entry = generic.find_user("alice")
    assert entry is not None
    assert entry.subject == str(uuid.uuid5(uuid.NAMESPACE_DNS, "alice"))
    assert entry.claims() == {
        "groups": ["Finance", "admins"],
        "memberOf": ["cn=Finance,ou=groups,dc=ex", "cn=admins,ou=groups,dc=ex"],
        "email": ["alice@example.org"],
    }


def test_password_is_proved_only_by_a_bind(generic: Ldap3Directory) -> None:
    dn = f"uid=alice,{BASE}"
    assert generic.verify_password(dn, "alice-pw")
    assert not generic.verify_password(dn, "wrong")
    assert not generic.verify_password(dn, "")


def test_a_refused_service_bind_is_unavailable() -> None:
    directory = _mock_directory(GENERIC, OFFLINE_SLAPD_2_4, {})
    directory._bind_password = "not-the-password"
    with pytest.raises(DirectoryUnavailable):
        directory.find_user("alice")


def _ad_user(name: str, control: int) -> dict[str, Any]:
    return {
        "objectClass": ["top", "person", "user"],
        "sAMAccountName": name,
        "userPassword": f"{name}-pw",
        "objectGUID": uuid.uuid5(uuid.NAMESPACE_DNS, name).bytes_le,
        "userAccountControl": control,
        "memberOf": ["CN=Finance,OU=Groups,DC=corp,DC=ex"],
    }


@pytest.fixture
def active_directory() -> Ldap3Directory:
    return _mock_directory(
        AD,
        OFFLINE_AD_2012_R2,
        {
            f"CN=Carol,{AD_BASE}": _ad_user("carol", 512),
            f"CN=Dave,{AD_BASE}": _ad_user("dave", 514),
        },
    )


def test_starttls_precedes_every_bind(active_directory: Ldap3Directory) -> None:
    STARTTLS_CALLS.clear()
    active_directory.find_user("carol")
    assert active_directory.verify_password(f"CN=Carol,{AD_BASE}", "carol-pw")
    assert STARTTLS_CALLS == ["CN=svc,DC=corp,DC=ex", f"CN=Carol,{AD_BASE}"]


def test_failed_starttls_never_binds() -> None:
    directory = _mock_directory(AD, OFFLINE_AD_2012_R2, {})
    connect = directory._connect

    def broken(user: str, password: str) -> Any:
        connection = connect(user, password)

        def refuse() -> bool:
            from ldap3.core.exceptions import LDAPStartTLSError

            raise LDAPStartTLSError("no tls")

        connection.start_tls = refuse
        connection.bind = lambda: pytest.fail("bound without TLS")
        return connection

    directory._connect = broken
    with pytest.raises(DirectoryUnavailable):
        directory.find_user("carol")


def test_ad_guid_and_disabled_bit(active_directory: Ldap3Directory) -> None:
    carol = active_directory.find_user("carol")
    dave = active_directory.find_user("dave")
    assert carol is not None and carol.active
    assert carol.subject == str(uuid.uuid5(uuid.NAMESPACE_DNS, "carol"))
    assert carol.groups == ("Finance",)
    assert dave is not None and not dave.active


def test_users_pages_through_the_base(generic: Ldap3Directory) -> None:
    assert sorted(entry.username for entry in generic.users()) == ["a*", "alice", "bob"]


# ---------------------------------------------------------------------------
# The served sign-in route
# ---------------------------------------------------------------------------
def _world(directory: Any, config: dict[str, Any]) -> SimpleNamespace:
    port = FakeIdentityPort([idp_wire("corp", "ldap", config, secret_ref="ldap/bind")])
    secrets = FakeSecrets({"ldap/bind": "svc-pw"})
    broker = LdapBroker(
        directory=IdpDirectory(port),
        directories=lambda record: directory,
        completer=EngineLoginCompleter(port),
        throttle=FailureThrottle(secrets),
    )
    client = TestClient(Starlette(routes=broker.routes()), base_url="https://graphos.example", follow_redirects=False)
    return SimpleNamespace(port=port, client=client)


def _login(world: SimpleNamespace, username: str, password: str) -> Any:
    return world.client.post("/auth/ldap/corp/login", data={"username": username, "password": password})


def test_bind_sign_in_reaches_the_engine(generic: Ldap3Directory) -> None:
    world = _world(generic, GENERIC)
    response = _login(world, "alice", "alice-pw")
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert SESSION_COOKIE in response.cookies
    (login,) = world.port.ops("credential", "external_login")
    assert login["request"]["idp_id"] == "corp"
    assert login["request"]["claims"]["groups"] == ["Finance", "admins"]
    assert login["request"]["username_hint"] == "alice"


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong"), ("alice", ""), ("nobody", "x"), ("*", "alice-pw"), ("alice)(uid=*", "alice-pw")],
)
def test_bad_credentials_never_reach_the_engine(generic: Ldap3Directory, username: str, password: str) -> None:
    world = _world(generic, GENERIC)
    response = _login(world, username, password)
    assert response.headers["location"] == "/auth/login?error=denied"
    assert world.port.ops("credential", "external_login") == []


def test_disabled_ad_account_cannot_sign_in(active_directory: Ldap3Directory) -> None:
    world = _world(active_directory, AD)
    assert _login(world, "carol", "carol-pw").headers["location"] == "/"
    assert _login(world, "dave", "dave-pw").headers["location"] == "/auth/login?error=denied"


def test_repeated_failures_are_throttled(generic: Ldap3Directory) -> None:
    world = _world(generic, GENERIC)
    for _ in range(6):
        _login(world, "alice", "wrong")
    response = _login(world, "alice", "alice-pw")
    assert response.headers["location"] == "/auth/login?error=throttled"


def test_unknown_idp_is_unavailable(generic: Ldap3Directory) -> None:
    world = _world(generic, GENERIC)
    response = world.client.post("/auth/ldap/other/login", data={"username": "alice", "password": "alice-pw"})
    assert response.headers["location"] == "/auth/login?error=idp_unavailable"


# ---------------------------------------------------------------------------
# Scheduled sync
# ---------------------------------------------------------------------------
class ListDirectory:
    def __init__(self, entries: list[DirectoryEntry], *, broken: bool = False) -> None:
        self.entries = entries
        self.broken = broken

    def users(self) -> Iterator[DirectoryEntry]:
        if self.broken:
            raise DirectoryUnavailable("down")
        return iter(self.entries)


def _entry(name: str, *, active: bool = True, groups: tuple[str, ...] = ()) -> DirectoryEntry:
    return DirectoryEntry(dn=f"uid={name},{BASE}", subject=f"s-{name}", username=name, active=active, groups=groups)


def _sync_world(entries: list[DirectoryEntry], provisioned: list[str], **kw: Any) -> SimpleNamespace:
    port = FakeIdentityPort([idp_wire("corp", "ldap", GENERIC | kw.pop("config", {}), secret_ref="ldap/bind")])
    rows = [{"subject": f"s-{n}", "user": {"principal_id": f"usr:{n}", "username": n, "status": "active"}} for n in provisioned]
    port.handlers[("idp", "list_provisioned")] = lambda op: {"kind": "provisioned", "value": rows}
    port.handlers[("idp", "provision")] = lambda op: {"kind": "user", "value": {}}
    directory = ListDirectory(entries, **kw)
    sync = LdapSync(port=port, directory=IdpDirectory(port), directories=lambda record: directory)
    return SimpleNamespace(port=port, sync=sync)


async def _run(world: SimpleNamespace) -> Any:
    (record,) = await IdpDirectory(world.port).records()
    return await world.sync.sync(record)


async def test_sync_provisions_groups_and_deprovisions_disabled() -> None:
    world = _sync_world([_entry("alice", groups=("Finance",)), _entry("dave", active=False)], ["alice", "dave"])
    report = await _run(world)
    sent = {op["request"]["subject"]: op["request"] for op in world.port.ops("idp", "provision")}
    assert sent["s-alice"]["active"] and sent["s-alice"]["claims"]["groups"] == ["Finance"]
    assert sent["s-dave"]["active"] is False
    assert (report.provisioned, report.deprovisioned, report.aborted) == (1, 1, None)


async def test_group_removed_in_directory_is_resent_without_it() -> None:
    world = _sync_world([_entry("alice")], ["alice"])
    await _run(world)
    (op,) = world.port.ops("idp", "provision")
    assert "groups" not in op["request"]["claims"]


async def test_user_gone_from_directory_is_deprovisioned() -> None:
    names = [f"u{i}" for i in range(8)]
    world = _sync_world([_entry(n) for n in names[:-1]], names)
    report = await _run(world)
    gone = [op["request"] for op in world.port.ops("idp", "provision") if op["request"]["subject"] == "s-u7"]
    assert gone == [{"idp_id": "corp", "subject": "s-u7", "username": "u7", "active": False, "claims": {}}]
    assert report.deprovisioned == 1


async def test_mass_deprovision_is_refused() -> None:
    world = _sync_world([], ["alice", "bob", "carol"])
    report = await _run(world)
    assert report.aborted and "max_deprovision_ratio" in report.aborted
    assert world.port.ops("idp", "provision") == []


async def test_directory_outage_changes_nothing() -> None:
    world = _sync_world([_entry("alice")], ["alice"], broken=True)
    report = await _run(world)
    assert report.aborted is not None
    assert world.port.ops("idp", "provision") == []
    assert world.port.ops("idp", "list_provisioned") == []


async def test_sync_due_respects_the_interval() -> None:
    world = _sync_world([_entry("alice")], ["alice"])
    records = await IdpDirectory(world.port).records()
    assert len(await world.sync.sync_due(records)) == 1
    assert await world.sync.sync_due(records) == []

