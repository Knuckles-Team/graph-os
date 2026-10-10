"""Spec-bound tests for the pure directory sync plan (R010.2.3.1)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.ldap import DirectoryUser, plan_directory_sync

_ADMINS = "cn=admins,ou=groups,dc=example,dc=org"
_MAPPING = {_ADMINS: {"admin"}}


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.3.1")
def test_plan_assigns_roles_and_deprovisions_disabled() -> None:
    plan = plan_directory_sync(
        [
            DirectoryUser("alice", (_ADMINS.upper(),)),
            DirectoryUser("bob", (_ADMINS,), disabled=True),
            DirectoryUser("carol", ("cn=other,dc=example,dc=org",)),
        ],
        _MAPPING,
    )
    assert dict(plan.assignments) == {"alice": frozenset({"admin"})}
    assert plan.deprovision == ("bob",)
    assert plan.unmapped == ("carol",)


@pytest.mark.spec("GRAPHOS-IDENTITY-R010.2.3.1")
def test_empty_snapshot_is_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        plan_directory_sync([], _MAPPING)
