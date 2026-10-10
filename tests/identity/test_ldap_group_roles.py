"""Spec-bound tests for directory group-DN-to-role mapping (R010.2.1)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.ldap import map_groups_to_roles

_ADMINS = "cn=admins,ou=groups,dc=example,dc=org"
_OPS = "cn=ops,ou=groups,dc=example,dc=org"
_MAPPING = {_ADMINS: {"admin"}, _OPS: ["operator", "viewer"]}


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.1")
def test_matching_groups_union_their_roles() -> None:
    roles = map_groups_to_roles([_ADMINS, _OPS], _MAPPING)
    assert roles == frozenset({"admin", "operator", "viewer"})


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.1")
def test_dn_match_ignores_case_and_rdn_spacing() -> None:
    roles = map_groups_to_roles(["CN=Admins, OU=Groups , DC=Example,DC=org"], _MAPPING)
    assert roles == frozenset({"admin"})


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.1")
@pytest.mark.parametrize("groups", [[], ["cn=other,dc=example,dc=org"]])
def test_no_matching_group_is_refused(groups: list[str]) -> None:
    with pytest.raises(IdentityUnavailable):
        map_groups_to_roles(groups, _MAPPING)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.1")
def test_matching_group_with_no_roles_is_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        map_groups_to_roles([_ADMINS], {_ADMINS: set()})
