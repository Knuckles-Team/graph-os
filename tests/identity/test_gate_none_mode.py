"""``none`` mode through the served gate (IDM-07).

The ``none`` authenticator is not "auth off": the request reaches the
application as the bootstrap principal carrying a local-issuer token, the same
claims shape a signed-in administrator gets.
"""

from __future__ import annotations

import asyncio

from graph_os.identity.setup_gate import seed_first_boot

from .gate_harness import Served, serve
from .store_double import StoreDouble


def _none_mode() -> Served:
    served = serve(StoreDouble())
    assert asyncio.run(seed_first_boot(served.runtime.admission, "none")) == "none"
    return served


def test_first_boot_seeds_none_and_it_is_idempotent() -> None:
    served = _none_mode()
    assert asyncio.run(seed_first_boot(served.runtime.admission, "none")) == "none"
    assert served.store.config and served.store.config["mode"] == "none"


def test_uncredentialed_request_reaches_the_app_as_the_bootstrap_principal() -> None:
    served = _none_mode()
    body = served.get("/api/echo").json()
    assert body["sub"] == "usr:bootstrap"
    assert body["authorization"].startswith("Bearer ")
    assert "identity:admin" in body["scope"].split()
    assert body["amr"] == ["none"]
    assert served.session, "the demo session cookie is handed out"


def test_bootstrap_token_has_the_same_claims_shape_as_a_signed_in_token() -> None:
    served = _none_mode()
    token = served.get("/api/echo").json()["authorization"].split(" ", 1)[1]
    claims = served.runtime.broker.issuer.verify(token)
    assert {
        "iss",
        "sub",
        "aud",
        "exp",
        "scope",
        "realm_access",
        "tenant_id",
        "amr",
        "auth_time",
    } <= set(claims)


def test_dns_rebinding_host_is_refused() -> None:
    served = _none_mode()
    response = served.client.get("/api/echo", headers={"host": "attacker.example"})
    assert response.status_code == 403
    assert response.json() == {"error": "host_not_loopback"}


def test_identity_endpoints_are_guarded_too() -> None:
    served = _none_mode()
    response = served.client.get("/auth/session", headers={"host": "attacker.example"})
    assert response.status_code == 403


def test_cross_origin_state_change_is_refused() -> None:
    served = _none_mode()
    response = served.client.post(
        "/api/echo", headers={"origin": "https://attacker.example"}
    )
    assert response.status_code == 403


def test_session_status_reports_the_mode_and_banner() -> None:
    served = _none_mode()
    body = served.get("/auth/session").json()
    assert body["mode"] == "none"
    assert body["authenticated"] is True
    assert body["subject"] == "usr:bootstrap"
    assert body["webui_role"] == "admin"
    assert "administrator" in body["banner"]


def test_one_demo_session_is_shared_and_reopened_after_revocation() -> None:
    served = _none_mode()
    served.get("/api/echo")
    first = served.session
    served.session = None
    served.get("/api/echo")
    assert served.session == first
    for session in served.store.sessions.values():
        session.revoked = True
    assert served.get("/api/echo").json()["sub"] == "usr:bootstrap"
    assert served.session != first


def test_store_outage_fails_closed() -> None:
    served = _none_mode()
    served.store.available = False
    served.runtime.broker.forget_config()
    response = served.client.get("/api/echo")
    assert response.status_code == 503
    assert response.json() == {"error": "identity_unavailable"}
