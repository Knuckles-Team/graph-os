"""Bound tests for the SCIM handlers over a fake identity port (R011.2.3)."""

import pytest

from graph_os.identity.engine import IdentityUnavailable
from graph_os.identity.scim import (
    SCIM_USER_SCHEMA,
    ScimUser,
    scim_create_user,
    scim_deactivate_user,
    scim_patch_user,
)

_ID = "GRAPHOS-IDENTITY-R011.2.3"
_PAYLOAD = {"schemas": [SCIM_USER_SCHEMA], "userName": "ada", "active": True}


class _FakePort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def create_user(self, user: ScimUser) -> str:
        self.calls.append(("create", user))
        return "u1"

    def update_user(self, user_id: str, user: ScimUser) -> None:
        self.calls.append(("update", (user_id, user)))

    def deactivate_user(self, user_id: str) -> None:
        self.calls.append(("deactivate", user_id))


@pytest.mark.spec(_ID)
def test_create_calls_port_with_validated_user() -> None:
    port = _FakePort()
    assert scim_create_user(port, _PAYLOAD) == "u1"
    assert port.calls == [("create", ScimUser(user_name="ada"))]


@pytest.mark.spec(_ID)
def test_patch_calls_port_update() -> None:
    port = _FakePort()
    scim_patch_user(port, "u1", {**_PAYLOAD, "active": False})
    assert port.calls == [("update", ("u1", ScimUser("ada", active=False)))]


@pytest.mark.spec(_ID)
def test_deactivate_calls_port() -> None:
    port = _FakePort()
    scim_deactivate_user(port, "u1")
    assert port.calls == [("deactivate", "u1")]


@pytest.mark.spec(_ID)
def test_invalid_payload_refused_before_port() -> None:
    port = _FakePort()
    with pytest.raises(IdentityUnavailable):
        scim_create_user(port, {"userName": "ada"})
    with pytest.raises(IdentityUnavailable):
        scim_patch_user(port, "u1", {"schemas": [SCIM_USER_SCHEMA]})
    assert port.calls == []


@pytest.mark.spec(_ID)
def test_missing_port_refused() -> None:
    with pytest.raises(IdentityUnavailable):
        scim_create_user(None, _PAYLOAD)
    with pytest.raises(IdentityUnavailable):
        scim_patch_user(None, "u1", _PAYLOAD)
    with pytest.raises(IdentityUnavailable):
        scim_deactivate_user(None, "u1")
