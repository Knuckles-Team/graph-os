"""The external authorities mounted behind the identity gate (identity-external B)."""

from __future__ import annotations

import asyncio

import pytest

from graph_os.identity.browser import SESSION_COOKIE
from graph_os.identity.engine import IdentityCall, IdentityRefused, IdentityUnavailable
from graph_os.identity.engine_ports import BrokerPort
from graph_os.identity.gate import owned_route_refusal
from graph_os.identity.idp_common import IdentityRefused as AuthorityRefused
from graph_os.identity.serving import prepare_identity

from .gate_harness import BASE, serve
from .store_double import StoreDouble

SESSION = "s" * 43


def _scope(path: str, method: str = "POST", **headers: str) -> dict[str, object]:
    raw = [
        (name.replace("_", "-").encode(), value.encode())
        for name, value in headers.items()
    ]
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "https",
        "headers": raw,
    }


@pytest.mark.parametrize(
    "path", ["/scim/v2/Users", "/scim/v2/Groups/g1", "/oauth/token", "/auth/saml/acs"]
)
def test_self_authenticated_routes_skip_origin_and_csrf(path: str) -> None:
    assert owned_route_refusal(_scope(path, host="app.test")) is None


@pytest.mark.parametrize(
    "path",
    ["/auth/login", "/auth/setup", "/auth/ldap/corp/login", "/auth/password/reset"],
)
def test_pre_session_forms_need_only_a_same_origin_origin(path: str) -> None:
    cookie = f"{SESSION_COOKIE}={SESSION}"
    assert (
        owned_route_refusal(_scope(path, host="app.test")) == "origin_not_same_origin"
    )
    same = _scope(path, host="app.test", origin="https://app.test", cookie=cookie)
    assert owned_route_refusal(same) is None


def test_session_bearing_owned_state_change_needs_the_csrf_token() -> None:
    cookie = f"{SESSION_COOKIE}={SESSION}"
    scope = _scope(
        "/auth/oidc/logout", host="app.test", origin="https://app.test", cookie=cookie
    )
    assert owned_route_refusal(scope) == "csrf_token_mismatch"


def test_safe_methods_are_never_refused() -> None:
    assert (
        owned_route_refusal(_scope("/auth/oidc/kc/login", "GET", host="app.test"))
        is None
    )


def test_external_routes_are_mounted_and_scim_reaches_its_own_auth() -> None:
    served = serve(StoreDouble(), profile="single-node-prod")
    served.post(
        "/auth/setup",
        {
            "setup_code": "operator-setup-code",
            "username": "root",
            "password": "correct horse battery",
        },
    )
    scim = served.client.get("/scim/v2/Users", headers={"authorization": "Bearer nope"})
    assert scim.status_code == 401
    assert scim.headers["content-type"].startswith("application/scim+json")


def test_broker_redirect_pages_carry_only_fixed_codes() -> None:
    served = serve(StoreDouble(), profile="single-node-prod")
    served.client.follow_redirects = False
    page = served.client.get("/auth/login?error=idp_refused")
    assert (
        page.status_code == 303
        and page.headers["location"] == "/?auth=sign-in&error=idp_refused"
    )
    evil = served.client.get("/auth/login?error=%3Cscript%3E")
    assert evil.headers["location"] == "/?auth=sign-in"
    mfa = served.client.get("/auth/mfa")
    assert mfa.headers["location"] == "/?auth=second-factor"


def test_session_status_reports_the_mail_features() -> None:
    served = serve(StoreDouble())
    asyncio.run(prepare_identity(served.runtime, ["127.0.0.1"]))
    body = served.client.get("/auth/session", headers={"origin": BASE}).json()
    assert body["features"] == {
        "password_reset_email": False,
        "email_verification": False,
        "invite_email": False,
    }


def test_prepare_starts_the_ldap_sync() -> None:
    served = serve(StoreDouble())

    async def prepare_and_look() -> bool:
        await prepare_identity(served.runtime, ["127.0.0.1"])
        task = served.runtime.background[-1]
        await asyncio.sleep(0)
        running = not task.done()
        task.cancel()
        return running

    assert asyncio.run(prepare_and_look())


class _Engine:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def broker(self, call: IdentityCall) -> object:
        raise self._error

    async def as_caller(self, session: object, call: IdentityCall) -> object:
        raise self._error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (IdentityRefused("IDENTITY_COLLISION"), "collision"),
        (IdentityUnavailable("down"), "unavailable"),
    ],
)
def test_broker_port_raises_the_authorities_one_refusal_type(
    error: Exception, code: str
) -> None:
    port = BrokerPort(_Engine(error))
    with pytest.raises(AuthorityRefused) as refused:
        asyncio.run(port.call({"family": "idp", "op": "list"}))
    assert refused.value.code == code
