"""Spec-bound validation tests for the LDAPS bind-config model (R010.1)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.ldap import LdapBindConfig

_DN = "cn=svc,dc=example,dc=org"


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.1")
@pytest.mark.parametrize("scheme", ["ldap", "http", ""])
def test_non_ldaps_scheme_is_refused(scheme: str) -> None:
    with pytest.raises(IdentityUnavailable):
        LdapBindConfig(host="dc.example.org", port=636, bind_dn=_DN, scheme=scheme)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.1")
@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_invalid_port_is_refused(port: int) -> None:
    with pytest.raises(IdentityUnavailable):
        LdapBindConfig(host="dc.example.org", port=port, bind_dn=_DN)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.1")
def test_valid_ldaps_config_is_accepted_at_port_bounds() -> None:
    for port in (1, 636, 65535):
        config = LdapBindConfig(host="dc.example.org", port=port, bind_dn=_DN)
        assert (config.scheme, config.port) == ("ldaps", port)
