"""Strict resolution parsing never fills missing authority fields."""

from copy import deepcopy

import pytest

from graph_os.identity.engine import IdentityUnavailable, Resolution


def resolution_value():
    """Public response shape only: this fixture is not a verified EG owner."""
    return {
        "principal_id": "usr:fixture",
        "username": "fixture",
        "kind": "human",
        "status": "active",
        "is_bootstrap": False,
        "roles": ["reader"],
        "groups": [],
        "scopes": ["kg:read"],
        "mfa_required": False,
        "mfa_enrolled": False,
        "session_mfa_pending": False,
        "request_context": {
            "principal": "usr:fixture",
            "tenant": "fixture-tenant",
            "audience": "fixture-audience",
            "agent_id": "usr:fixture",
            "roles": ["reader"],
            "scopes": ["kg:read"],
            "policy_version": "fixture-policy",
            "delegation": [],
        },
    }


@pytest.mark.parametrize("kind", ["human", "service"])
def test_explicit_kind_and_copied_immutable_context(kind):
    value = resolution_value()
    value["kind"] = kind
    result = Resolution.from_reply({"kind": "resolution", "value": value})
    value["request_context"]["scopes"].append("kg:admin")
    value["roles"].append("admin")
    assert result.kind == kind
    assert result.scopes == ("kg:read",)
    assert result.request_context["scopes"] == ("kg:read",)
    assert result.roles == ("reader",)
    with pytest.raises(TypeError):
        result.request_context["policy_version"] = "forged"


@pytest.mark.parametrize("name", tuple(resolution_value()))
def test_all_resolution_fields_are_required(name):
    value = resolution_value()
    del value[name]
    with pytest.raises(IdentityUnavailable):
        Resolution.parse(value)


@pytest.mark.parametrize("name", tuple(resolution_value()["request_context"]))
def test_all_context_fields_are_required(name):
    value = resolution_value()
    del value["request_context"][name]
    with pytest.raises(IdentityUnavailable):
        Resolution.parse(value)


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("kind", "email"),
        ("principal_id", 7),
        ("roles", "reader"),
        ("roles", ["reader", "reader"]),
        ("scopes", ["kg:*"]),
        ("scopes", ["kg:read kg:write"]),
        ("mfa_enrolled", 1),
        ("session_mfa_pending", "false"),
        ("is_bootstrap", None),
    ],
)
def test_malformed_fields_are_not_coerced(name, invalid):
    value = resolution_value()
    value[name] = invalid
    with pytest.raises(IdentityUnavailable):
        Resolution.parse(value)


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("principal", "usr:other"),
        ("agent_id", "svc:other"),
        ("delegation", ["usr:fixture", "svc:other"]),
        ("delegation", None),
        ("policy_version", ""),
        ("roles", []),
        ("scopes", ["kg:admin"]),
        ("scopes", ["kg:read", "kg:read"]),
    ],
)
def test_context_mismatch_or_delegation_refuses(name, invalid):
    value = resolution_value()
    value["request_context"][name] = invalid
    with pytest.raises(IdentityUnavailable):
        Resolution.parse(value)


@pytest.mark.parametrize("name", ["oidc_token", "principal_kind", "tenant_id"])
def test_jwt_fields_do_not_leak_into_strict_context(name):
    value = resolution_value()
    value["request_context"][name] = "not-a-context-field"
    with pytest.raises(IdentityUnavailable):
        Resolution.parse(value)


@pytest.mark.parametrize("change", ["disabled", "pending", "unenrolled"])
def test_unusable_identity_is_not_an_issuance_resolution(change):
    value = resolution_value()
    if change == "disabled":
        value["status"] = "disabled"
    elif change == "pending":
        value["session_mfa_pending"] = True
    else:
        value["mfa_required"] = True
    with pytest.raises(PermissionError):
        Resolution.parse(value)


def test_narrowing_changes_both_scope_sets_without_role_expansion():
    value = resolution_value()
    value["roles"] = value["request_context"]["roles"] = ["kg:admin"]
    original = deepcopy(value)
    result = Resolution.parse(value).narrow(("kg:admin",))
    assert result.scopes == result.request_context["scopes"] == ()
    assert result.roles == ("kg:admin",)
    assert value == original


def test_missing_and_wrong_reply_tag_refuse():
    for reply in ({"value": resolution_value()}, {"kind": "principal", "value": {}}):
        with pytest.raises(IdentityUnavailable):
            Resolution.from_reply(reply)
