"""Bound tests for the typed SCIM User resource model (R011.2.1)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.scim import SCIM_USER_SCHEMA, ScimUser

_ID = "GRAPHOS-IDENTITY-R011.2.1"


def _payload(**extra: object) -> dict[str, object]:
    return {"schemas": [SCIM_USER_SCHEMA], "userName": "alice", **extra}


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_parses_valid_payload() -> None:
    user = ScimUser.from_payload(_payload(externalId="x1", active=False))
    assert (user.user_name, user.active, user.external_id) == ("alice", False, "x1")


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_defaults_active() -> None:
    assert ScimUser.from_payload(_payload()).active is True


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_refuses_missing_schema() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimUser.from_payload({"userName": "alice"})


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_refuses_blank_user_name() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimUser(user_name="  ")
    with pytest.raises(IdentityUnavailable):
        ScimUser.from_payload({"schemas": [SCIM_USER_SCHEMA]})


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_refuses_non_boolean_active() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimUser.from_payload(_payload(active="false"))


@pytest.mark.spec("GRAPHOS-IDENTITY-R011.2.1")
def test_scim_user_refuses_empty_external_id() -> None:
    with pytest.raises(IdentityUnavailable):
        ScimUser(user_name="alice", external_id="")
