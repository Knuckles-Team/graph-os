"""``local`` mode through the served gate: setup, sessions, MFA, API keys (IDM-08/09/11)."""

from __future__ import annotations

from .gate_harness import SETUP_CODE, Served, serve
from .store_double import TOTP_GOOD_CODE, StoreDouble

ADMIN = {"username": "root", "password": "correct horse battery staple"}


def _fresh() -> Served:
    return serve(StoreDouble(), profile="single-node-prod")


def _with_admin() -> Served:
    served = _fresh()
    response = served.post("/auth/setup", {"setup_code": SETUP_CODE, **ADMIN})
    assert response.status_code == 200, response.text
    return served


def test_fresh_production_instance_requires_setup() -> None:
    served = _fresh()
    body = served.get("/auth/session").json()
    assert body == {"authenticated": False, "mode": None, "setup_required": True}
    assert served.get("/api/echo").json()["authorization"] is None


def test_setup_needs_the_operator_code_and_runs_once() -> None:
    served = _fresh()
    wrong = served.post("/auth/setup", {"setup_code": "guess", **ADMIN})
    assert wrong.status_code == 403
    assert (
        served.post("/auth/setup", {"setup_code": SETUP_CODE, **ADMIN}).status_code
        == 200
    )
    again = served.post("/auth/setup", {"setup_code": SETUP_CODE, **ADMIN})
    assert again.status_code == 409


def test_setup_refuses_a_cross_origin_form() -> None:
    served = _fresh()
    response = served.client.post(
        "/auth/setup",
        json={"setup_code": SETUP_CODE, **ADMIN},
        headers={"origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_setup_signs_the_first_administrator_in() -> None:
    served = _with_admin()
    body = served.get("/api/echo").json()
    assert "identity:admin" in body["scope"].split()
    assert body["amr"] == ["session"]
    status = served.get("/auth/session").json()
    assert status["mode"] == "local" and status["authenticated"] is True
    assert status["banner"] is None


def test_wrong_password_and_unknown_user_answer_the_same() -> None:
    served = _with_admin()
    served.session = None
    unknown = served.post("/auth/login", {"username": "nobody", "password": "x" * 12})
    wrong = served.post("/auth/login", {"username": "root", "password": "x" * 12})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"outcome": "bad"}
    assert served.session is None


def test_uncredentialed_request_in_local_mode_carries_no_principal() -> None:
    served = _with_admin()
    served.session = None
    assert served.get("/api/echo").json()["authorization"] is None


def test_logout_revokes_server_side_within_one_request() -> None:
    served = _with_admin()
    stolen = served.session
    assert served.post("/auth/logout").status_code == 200
    served.session = stolen
    response = served.get("/api/echo")
    assert response.json()["authorization"] is None
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_cookie_state_change_needs_the_csrf_token() -> None:
    served = _with_admin()
    served.csrf = None
    assert served.post("/api/echo").status_code == 403
    served.csrf = "forged"
    assert served.post("/api/echo").status_code == 403


def test_cookie_request_with_the_token_reaches_the_app() -> None:
    served = _with_admin()
    assert served.post("/api/echo").json()["sub"] is not None


def test_login_rotates_the_session_id() -> None:
    served = _with_admin()
    first = served.session
    served.post("/auth/login", ADMIN)
    assert served.session and served.session != first


def test_admin_reset_token_is_single_use() -> None:
    served = _with_admin()
    user = served.store.add_user("bob", "old password here")
    token = served.post(
        "/auth/admin/reset", {"principal_id": user.principal_id}
    ).json()["token"]
    reset = {
        "purpose": "admin_reset",
        "token": token,
        "new_password": "new password here",
    }
    assert served.post("/auth/password/reset", reset).status_code == 200
    assert served.post("/auth/password/reset", reset).status_code == 400
    assert user.password == "new password here"


def test_forgot_password_is_uniform_without_email() -> None:
    served = _with_admin()
    served.session = None
    body = served.post("/auth/password/forgot", {"username": "whoever"}).json()
    assert body == {
        "email_reset": False,
        "alternatives": ["recovery_code", "admin_reset"],
    }


def test_api_key_is_narrowed_and_revocable() -> None:
    served = _with_admin()
    admin = next(u for u in served.store.users.values() if u.username == "root")
    issued = served.post(
        "/auth/api-keys", {"principal_id": admin.principal_id, "scopes": ["kg:admin"]}
    )
    assert issued.status_code == 201
    key = issued.json()["api_key"]
    assert key.startswith("gok_")
    served.session = None
    as_key = served.get("/api/echo", authorization=f"Bearer {key}").json()
    assert as_key["scope"] == "kg:admin" and as_key["amr"] == ["api_key"]
    admin.scopes = admin.scopes - {"kg:admin"}
    narrowed = served.get("/api/echo", authorization=f"Bearer {key}").json()
    assert narrowed["scope"] == ""
    key_id = issued.json()["key_id"]
    served.store.api_keys[key_id] = (*served.store.api_keys[key_id][:3], True)
    assert served.get("/api/echo", authorization=f"Bearer {key}").status_code == 401


def test_api_key_scopes_cannot_exceed_the_owner() -> None:
    served = _with_admin()
    bob = served.store.add_user("bob", "pw")
    response = served.post(
        "/auth/api-keys", {"principal_id": bob.principal_id, "scopes": ["kg:admin"]}
    )
    assert response.status_code == 403


def test_other_bearers_pass_through_untouched() -> None:
    served = _with_admin()
    served.session = None
    body = served.get("/api/echo", authorization="Bearer upstream.jwt.value").json()
    assert body["authorization"] == "Bearer upstream.jwt.value"
    assert body["sub"] is None


def test_duplicate_authorization_headers_are_refused() -> None:
    served = _with_admin()
    response = served.client.get(
        "/api/echo",
        headers=[("authorization", "Bearer a"), ("authorization", "Bearer b")],
    )
    assert response.status_code == 401


def test_token_exchange_trades_an_api_key() -> None:
    served = _with_admin()
    admin = next(u for u in served.store.users.values() if u.username == "root")
    key = served.post(
        "/auth/api-keys",
        {"principal_id": admin.principal_id, "scopes": ["identity:self"]},
    ).json()["api_key"]
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": key,
        "subject_token_type": "urn:graph-os:token-type:api-key",
    }
    response = served.client.post("/oauth/token", data=form)
    assert response.status_code == 200
    claims = served.runtime.broker.issuer.verify(response.json()["access_token"])
    assert claims["sub"] == admin.principal_id and claims["scope"] == "identity:self"
    form["subject_token"] = key + "x"
    assert served.client.post("/oauth/token", data=form).json() == {
        "error": "invalid_grant"
    }
    form["grant_type"] = "password"
    assert served.client.post("/oauth/token", data=form).status_code == 400


def _enroll_totp(served: Served) -> None:
    assert (
        served.post("/auth/mfa/totp/enroll")
        .json()["provisioning_uri"]
        .startswith("otpauth://totp/")
    )
    assert (
        served.post("/auth/mfa/totp/confirm", {"code": TOTP_GOOD_CODE}).status_code
        == 200
    )


def test_totp_second_factor_gates_the_session() -> None:
    served = _with_admin()
    _enroll_totp(served)
    served.session = None
    first = served.post("/auth/login", ADMIN)
    assert first.json()["outcome"] == "mfa_required"
    assert served.get("/api/echo").status_code == 401
    bad = served.post("/auth/mfa/verify", {"method": "totp", "code": "000000"})
    assert bad.status_code == 401
    assert served.post(
        "/auth/mfa/verify", {"method": "totp", "code": TOTP_GOOD_CODE}
    ).json() == {"outcome": "ok"}
    assert served.get("/api/echo").json()["sub"] is not None


def test_recovery_codes_are_single_use() -> None:
    served = _with_admin()
    _enroll_totp(served)
    codes = served.post("/auth/mfa/recovery-codes").json()["codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    for attempt, expected in ((codes[0], 200), (codes[0], 401)):
        served.session = None
        served.post("/auth/login", ADMIN)
        response = served.post(
            "/auth/mfa/verify", {"method": "recovery", "code": attempt}
        )
        assert response.status_code == expected


def test_webauthn_fails_closed() -> None:
    served = _with_admin()
    assert served.post("/auth/mfa/webauthn/register").status_code == 501


def test_registration_is_administrator_only() -> None:
    served = _with_admin()
    stolen_csrf = served.csrf
    served.session = None
    served.csrf = stolen_csrf
    anonymous = served.post("/auth/register", {"username": "eve"})
    assert anonymous.status_code == 401


def test_administrator_registers_a_user_under_their_own_authority() -> None:
    served = _with_admin()
    response = served.post(
        "/auth/register", {"username": "carol", "password": "x" * 16}
    )
    assert response.status_code == 201
    principal = response.json()["principal_id"]
    assert served.store.users[principal].username == "carol"
    admin = next(u for u in served.store.users.values() if u.username == "root")
    assert served.store.caller_calls[-1] == (admin.principal_id, "user", "create")


def test_self_service_password_change_and_key_revocation_run_as_the_caller() -> None:
    served = _with_admin()
    change = {"current": ADMIN["password"], "new": "a brand new passphrase"}
    assert served.post("/auth/password/change", change).status_code == 200
    admin = next(u for u in served.store.users.values() if u.username == "root")
    assert admin.password == "a brand new passphrase"
    issued = served.post(
        "/auth/api-keys",
        {"principal_id": admin.principal_id, "scopes": ["identity:self"]},
    ).json()
    response = served.client.delete(
        f"/auth/api-keys/{issued['key_id']}", headers=served.headers(state_change=True)
    )
    assert response.status_code == 200
    assert served.store.api_keys[issued["key_id"]][3] is True
    assert served.store.caller_calls[-1] == (
        admin.principal_id,
        "token",
        "revoke_api_key",
    )
