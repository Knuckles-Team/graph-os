"""The assembled external authorities: every IdP kind is data, the mounted
routes, sign-in options, and e-mail features hidden when mail is absent."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.identity.external import ExternalAuthorities, mail_settings_from_env
from tests.identity.fakes import FakeIdentityPort, FakeSecrets, idp_wire


class NoProvisioners:
    async def authenticate(self, authorization: str | None) -> Any:
        return None


def _authorities(
    idps: list[dict[str, Any]], mail: dict[str, str] | None = None
) -> tuple[ExternalAuthorities, TestClient]:
    port = FakeIdentityPort(idps)
    secrets = FakeSecrets({"mail/listmonk": "tok"})
    ext = ExternalAuthorities.build(
        port=port,
        secrets=secrets,
        provisioners=NoProvisioners(),
        mail_settings=mail or {},
    )
    client = TestClient(
        Starlette(routes=ext.routes()),
        base_url="https://graphos.example",
        follow_redirects=False,
    )
    return ext, client


def test_every_authority_is_mounted_once() -> None:
    ext, _ = _authorities([])
    paths = [route.path for route in ext.routes()]
    assert len(paths) == len(set(paths))
    for expected in (
        "/auth/idps",
        "/auth/oidc/{idp_id}/login",
        "/auth/oidc/callback",
        "/auth/saml/acs",
        "/auth/ldap/{idp_id}/login",
        "/scim/v2/Users",
        "/scim/v2/Groups/{resource_id}",
    ):
        assert expected in paths


def test_sign_in_options_list_every_enabled_browser_kind() -> None:
    idps = [
        idp_wire("keycloak", "oidc", {}, order=0, email_domains=["example.org"]),
        idp_wire("okta", "oidc", {}, order=1),
        idp_wire("adfs", "saml", {}, order=2),
        idp_wire("corp", "ldap", {}, order=3),
        idp_wire("scim-okta", "scim", {}, order=4),
        idp_wire("old", "oidc", {}, order=5, enabled=False),
    ]
    _, client = _authorities(idps)
    listed = client.get("/auth/idps").json()
    assert [i["idp_id"] for i in listed["idps"]] == ["keycloak", "okta", "adfs", "corp"]
    routed = client.get("/auth/idps", params={"email": "bob@Example.org"}).json()
    assert [i["idp_id"] for i in routed["idps"]] == ["keycloak"]


def test_no_mail_transport_reveals_no_email_feature() -> None:
    ext, client = _authorities([])
    assert client.get("/auth/idps").json()["features"] == {
        "password_reset_email": False,
        "email_verification": False,
        "invite_email": False,
    }
    assert not any(
        word in route.path
        for route in ext.routes()
        for word in ("forgot", "reset", "verify", "invite")
    )


def test_configured_mail_is_reported() -> None:
    mail = {
        "GRAPHOS_LISTMONK_URL": "https://listmonk.example",
        "GRAPHOS_LISTMONK_TOKEN_REF": "mail/listmonk",
        "GRAPHOS_LISTMONK_TEMPLATES": "password_reset=3",
    }
    _, client = _authorities([], mail)
    assert client.get("/auth/idps").json()["features"]["password_reset_email"] is True


def test_mail_settings_come_only_from_known_keys() -> None:
    env = {
        "GRAPHOS_SMTP_HOST": "smtp.example",
        "GRAPHOS_SMTP_PORT": "",
        "UNRELATED": "x",
    }
    assert mail_settings_from_env(env) == {"GRAPHOS_SMTP_HOST": "smtp.example"}


@pytest.mark.parametrize("kind", ["oidc", "saml"])
def test_an_unconfigured_kind_offers_nothing(kind: str) -> None:
    _, client = _authorities([])
    response = client.get(f"/auth/{kind}/absent/login")
    assert response.headers["location"] == "/auth/login?error=idp_unavailable"
