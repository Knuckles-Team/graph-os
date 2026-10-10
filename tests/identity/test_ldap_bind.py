"""Spec-bound tests for the injected directory bind and filter escaping (R010.2.2)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.ldap import LdapBindConfig, bind_directory, escape_filter_value

_CONFIG = LdapBindConfig(host="ldap.example.org", port=636, bind_dn="cn=svc,dc=x")


class _FakePort:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.bound: list[LdapBindConfig] = []

    def bind(self, config: LdapBindConfig) -> None:
        if self.fail:
            raise ConnectionError("down")
        self.bound.append(config)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.2")
def test_bind_goes_through_port() -> None:
    port = _FakePort()
    bind_directory(port, _CONFIG)
    assert port.bound == [_CONFIG]


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.2")
def test_missing_port_is_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        bind_directory(None, _CONFIG)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.2")
def test_bind_failure_is_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        bind_directory(_FakePort(fail=True), _CONFIG)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.2")
def test_filter_escaping_rfc4515() -> None:
    assert escape_filter_value("a*(b)\\c\x00") == r"a\2a\28b\29\5cc\00"
    assert escape_filter_value("plain") == "plain"
