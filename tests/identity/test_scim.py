"""SCIM 2.0 server: the RFC 7644 subset an Okta / Entra ID provisioning
connector exercises, through the served routes, against an in-memory engine
that enforces the provisioner binding.

Compliance subset covered: discovery documents; Users create / read / list /
filter (``userName``, ``externalId``, ``id`` ``eq``) / paging / replace /
PATCH (with and without a path, Entra ID's capitalised ops and string
booleans) / DELETE; Groups create / read / filter / PATCH members add, remove
(by path filter and by value) and replace / DELETE; uniqueness, invalidFilter,
invalidSyntax and auth errors.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.identity.idp_common import IdpDirectory
from graph_os.identity.scim import ApiKeyProvisionerAuth, ScimServer
from graph_os.identity.scim_schema import (
    PATCH_SCHEMA,
    ScimError,
    ScimGroup,
    ScimUser,
    apply_group_patch,
    apply_user_patch,
    parse_filter,
)
from tests.identity.fakes import ProvisioningEngine, idp_wire

PROVISIONER = "svc:okta-scim"
KEYS = {
    "gok_okta.s": SimpleNamespace(
        principal_id=PROVISIONER,
        kind="service",
        status="active",
        scopes={"identity:provision"},
    ),
    "gok_other.s": SimpleNamespace(
        principal_id="svc:other",
        kind="service",
        status="active",
        scopes={"identity:provision"},
    ),
    "gok_admin.s": SimpleNamespace(
        principal_id="usr:admin",
        kind="human",
        status="active",
        scopes={"identity:admin", "identity:provision"},
    ),
    "gok_wide.s": SimpleNamespace(
        principal_id=PROVISIONER,
        kind="service",
        status="active",
        scopes={"identity:admin", "*"},
    ),
    "gok_off.s": SimpleNamespace(
        principal_id=PROVISIONER,
        kind="service",
        status="disabled",
        scopes={"identity:provision"},
    ),
}


@pytest.fixture
def world() -> SimpleNamespace:
    engine = ProvisioningEngine(
        [
            idp_wire("okta", "scim", {"provisioner": PROVISIONER}),
            idp_wire("keycloak", "oidc", {}),
        ]
    )

    async def verify(token: str) -> Any:
        return KEYS.get(token)

    auth = ApiKeyProvisionerAuth(
        verify, lambda resolution: engine.port_for(resolution.principal_id)
    )
    directory_port = engine.port_for("broker")
    server = ScimServer(auth=auth, directory=IdpDirectory(directory_port))
    client = TestClient(
        Starlette(routes=server.routes()), base_url="https://graphos.example"
    )
    client.headers["Authorization"] = "Bearer gok_okta.s"
    return SimpleNamespace(engine=engine, client=client)


def _user(name: str, **extra: Any) -> dict[str, Any]:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": name,
        "name": {"givenName": name.title(), "familyName": "Doe"},
        "emails": [{"value": f"{name}@example.org", "primary": True}],
        "active": True,
    }
    return body | extra


def _create(world: SimpleNamespace, name: str, **extra: Any) -> dict[str, Any]:
    response = world.client.post("/scim/v2/Users", json=_user(name, **extra))
    assert response.status_code == 201, response.text
    assert response.headers["content-type"].startswith("application/scim+json")
    body: dict[str, Any] = response.json()
    assert response.headers["location"] == body["meta"]["location"]
    return body


def _patch(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [PATCH_SCHEMA], "Operations": list(operations)}


# ---------------------------------------------------------------------------
# Authentication and binding
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "header",
    [
        None,
        "Basic abc",
        "Bearer ",
        "Bearer gok_unknown.s",
        "Bearer gok_admin.s",
        "Bearer gok_wide.s",
        "Bearer gok_off.s",
    ],
)
def test_only_an_active_service_key_with_the_exact_scope_is_admitted(
    world: SimpleNamespace, header: str | None
) -> None:
    assert (
        world.client.get("/scim/v2/ServiceProviderConfig").status_code == 200
    )  # baseline twin
    headers = {"Authorization": header} if header else {}
    if header is None:
        del world.client.headers["Authorization"]
    response = world.client.get("/scim/v2/ServiceProviderConfig", headers=headers)
    assert response.status_code == 401
    assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_a_key_bound_to_no_scim_idp_is_forbidden(world: SimpleNamespace) -> None:
    response = world.client.get(
        "/scim/v2/Users", headers={"Authorization": "Bearer gok_other.s"}
    )
    assert response.status_code == 403


def test_discovery_documents(world: SimpleNamespace) -> None:
    config = world.client.get("/scim/v2/ServiceProviderConfig").json()
    assert config["patch"]["supported"] and not config["bulk"]["supported"]
    types = world.client.get("/scim/v2/ResourceTypes").json()["Resources"]
    assert {t["id"] for t in types} == {"User", "Group"}
    schemas = world.client.get("/scim/v2/Schemas").json()["Resources"]
    assert len(schemas) == 2


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def test_create_read_and_filter_users(world: SimpleNamespace) -> None:
    alice = _create(world, "alice", externalId="00u-alice")
    assert alice["userName"] == "alice" and alice["externalId"] == "00u-alice"
    assert alice["displayName"] == "Alice Doe"
    assert world.client.get(f"/scim/v2/Users/{alice['id']}").json()["id"] == alice["id"]
    _create(world, "bob")
    for query in (
        'userName eq "alice"',
        'externalId eq "00u-alice"',
        f'id eq "{alice["id"]}"',
        'USERNAME eq "alice"',
    ):
        listed = world.client.get("/scim/v2/Users", params={"filter": query}).json()
        assert [r["userName"] for r in listed["Resources"]] == ["alice"], query
    engine = world.engine
    assert engine.users[alice["id"]]["source"] == "scim:okta"
    assert engine.claims[alice["id"]] == {"email": ["alice@example.org"]}


def test_list_pages_with_start_index_and_count(world: SimpleNamespace) -> None:
    for name in ("a1", "a2", "a3"):
        _create(world, name)
    page = world.client.get(
        "/scim/v2/Users", params={"startIndex": 2, "count": 1}
    ).json()
    assert (page["totalResults"], page["startIndex"], page["itemsPerPage"]) == (3, 2, 1)
    assert page["Resources"][0]["userName"] == "a2"


def test_duplicate_user_is_a_uniqueness_conflict(world: SimpleNamespace) -> None:
    _create(world, "alice", externalId="x1")
    for body in (_user("alice"), _user("carol", externalId="x1")):
        response = world.client.post("/scim/v2/Users", json=body)
        assert (
            response.status_code == 409 and response.json()["scimType"] == "uniqueness"
        )


def test_rename_onto_a_taken_username_is_refused_by_the_engine(
    world: SimpleNamespace,
) -> None:
    _create(world, "alice")
    bob = _create(world, "bob")
    response = world.client.put(f"/scim/v2/Users/{bob['id']}", json=_user("alice"))
    assert response.status_code == 409


def test_entra_style_patch_deprovisions_and_keeps_the_principal(
    world: SimpleNamespace,
) -> None:
    alice = _create(world, "alice")
    body = _patch({"op": "Replace", "value": {"active": "False"}})
    response = world.client.patch(f"/scim/v2/Users/{alice['id']}", json=body)
    assert response.status_code == 200 and response.json()["active"] is False
    assert world.engine.revoked == [alice["id"]]
    again = world.client.get(f"/scim/v2/Users/{alice['id']}").json()
    assert again["active"] is False and again["userName"] == "alice"


def test_patch_with_paths(world: SimpleNamespace) -> None:
    alice = _create(world, "alice")
    body = _patch(
        {"op": "replace", "path": "displayName", "value": "Alice Liddell"},
        {
            "op": "replace",
            "path": 'emails[type eq "work"].value',
            "value": "al@example.org",
        },
    )
    patched = world.client.patch(f"/scim/v2/Users/{alice['id']}", json=body).json()
    assert patched["displayName"] == "Alice Liddell"
    assert patched["emails"][0]["value"] == "al@example.org"


def test_delete_deprovisions_never_deletes(world: SimpleNamespace) -> None:
    alice = _create(world, "alice")
    assert world.client.delete(f"/scim/v2/Users/{alice['id']}").status_code == 204
    assert alice["id"] in world.engine.users
    assert world.client.get(f"/scim/v2/Users/{alice['id']}").json()["active"] is False


def test_reactivation(world: SimpleNamespace) -> None:
    alice = _create(world, "alice")
    world.client.patch(
        f"/scim/v2/Users/{alice['id']}",
        json=_patch({"op": "replace", "path": "active", "value": False}),
    )
    back = world.client.patch(
        f"/scim/v2/Users/{alice['id']}",
        json=_patch({"op": "replace", "path": "active", "value": True}),
    )
    assert back.json()["active"] is True


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ('userName co "a"', "invalidFilter"),
        ('userName eq "a" and active eq true', "invalidFilter"),
        ('password eq "x"', "invalidFilter"),
    ],
)
def test_unsupported_filters_are_refused(
    world: SimpleNamespace, query: str, code: str
) -> None:
    response = world.client.get("/scim/v2/Users", params={"filter": query})
    assert response.status_code == 400 and response.json()["scimType"] == code


def test_malformed_bodies_are_refused(world: SimpleNamespace) -> None:
    bad = world.client.post(
        "/scim/v2/Users",
        content=b"{not json",
        headers={"Content-Type": "application/scim+json"},
    )
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidSyntax"
    missing = world.client.post("/scim/v2/Users", json={"schemas": []})
    assert missing.status_code == 400


def test_unknown_user_is_404(world: SimpleNamespace) -> None:
    assert world.client.get("/scim/v2/Users/usr:none").status_code == 404
    assert world.client.delete("/scim/v2/Users/usr:none").status_code == 404


def test_a_user_of_another_idp_is_invisible(world: SimpleNamespace) -> None:
    world.engine.links[("keycloak", "kc-sub")] = "usr:kc"
    world.engine.users["usr:kc"] = {
        "principal_id": "usr:kc",
        "username": "kc",
        "status": "active",
    }
    assert world.client.get("/scim/v2/Users/usr:kc").status_code == 404
    assert world.client.get("/scim/v2/Users").json()["totalResults"] == 0


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
def _group(world: SimpleNamespace, name: str, members: list[str]) -> dict[str, Any]:
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": name,
        "members": [{"value": m} for m in members],
    }
    response = world.client.post("/scim/v2/Groups", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_group_lifecycle_and_member_patches(world: SimpleNamespace) -> None:
    alice, bob = _create(world, "alice")["id"], _create(world, "bob")["id"]
    group = _group(world, "Finance", [alice])
    url = f"/scim/v2/Groups/{group['id']}"
    added = world.client.patch(
        url, json=_patch({"op": "add", "path": "members", "value": [{"value": bob}]})
    ).json()
    assert {m["value"] for m in added["members"]} == {alice, bob}
    removed = world.client.patch(
        url, json=_patch({"op": "remove", "path": f'members[value eq "{alice}"]'})
    ).json()
    assert [m["value"] for m in removed["members"]] == [bob]
    renamed = world.client.patch(
        url, json=_patch({"op": "replace", "value": {"displayName": "Treasury"}})
    ).json()
    assert renamed["displayName"] == "Treasury"
    listed = world.client.get(
        "/scim/v2/Groups", params={"filter": 'displayName eq "Treasury"'}
    ).json()
    assert listed["totalResults"] == 1
    assert world.client.delete(url).status_code == 204
    assert world.client.get(url).status_code == 404


def test_group_members_must_be_this_idps_users(world: SimpleNamespace) -> None:
    response = world.client.post(
        "/scim/v2/Groups",
        json={"displayName": "X", "members": [{"value": "usr:admin"}]},
    )
    assert response.status_code == 404


def test_duplicate_group_name_conflicts(world: SimpleNamespace) -> None:
    _group(world, "Finance", [])
    response = world.client.post("/scim/v2/Groups", json={"displayName": "Finance"})
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Pure schema helpers
# ---------------------------------------------------------------------------
def test_patch_refuses_unknown_paths_and_removing_username() -> None:
    user = ScimUser(user_name="alice")
    with pytest.raises(ScimError) as unknown:
        apply_user_patch(
            user, _patch({"op": "replace", "path": "roles", "value": "admin"})
        )
    assert unknown.value.scim_type == "invalidPath"
    with pytest.raises(ScimError) as immutable:
        apply_user_patch(user, _patch({"op": "remove", "path": "userName"}))
    assert immutable.value.scim_type == "mutability"
    with pytest.raises(ScimError):
        apply_user_patch(
            user, {"Operations": [{"op": "replace", "path": "active", "value": False}]}
        )


def test_group_patch_remove_by_value_and_replace() -> None:
    group = ScimGroup(display_name="G", members=frozenset({"a", "b", "c"}))
    removed = apply_group_patch(
        group, _patch({"op": "remove", "path": "members", "value": [{"value": "a"}]})
    )
    assert removed.members == {"b", "c"}
    replaced = apply_group_patch(
        group, _patch({"op": "replace", "path": "members", "value": [{"value": "z"}]})
    )
    assert replaced.members == {"z"}


def test_filter_unescapes_quotes() -> None:
    parsed = parse_filter('userName eq "a\\"b"', "User")
    assert parsed is not None and parsed.value == 'a"b'
