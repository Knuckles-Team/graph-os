"""Served WebAuthn admission for enrolled privileged browser sessions."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from graph_os.identity.browser import CSRF_HEADER

from .gate_harness import BASE, SETUP_CODE, serve
from .store_double import StoreDouble
from .test_webauthn_routes import ADMIN, _assertion, _credential


@pytest.mark.parametrize(
    ("username", "scope"),
    [("root", "identity:admin"), ("approver", "rbac:approve-elevation")],
)
def test_enrolled_privileged_browser_requires_signed_webauthn_and_refuses_replay(
    username: str, scope: str
) -> None:
    async def scenario() -> None:
        store = StoreDouble()
        served = serve(store, profile="single-node-prod")
        transport = httpx.ASGITransport(app=served.client.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:

            async def post(path: str, body: dict, csrf: str | None = None):
                headers = {"origin": BASE}
                if csrf is not None:
                    headers[CSRF_HEADER.decode()] = csrf
                return await client.post(path, json=body, headers=headers)

            setup = await post("/auth/setup", {"setup_code": SETUP_CODE, **ADMIN})
            assert setup.status_code == 200, setup.text
            if username == "approver":
                store.add_user(
                    username,
                    "correct horse battery staple",
                    scopes=frozenset({"kg:read", "identity:self", scope}),
                )
            client.cookies.clear()
            credentials = {
                "username": username,
                "password": "correct horse battery staple",
            }
            login = await post("/auth/login", credentials)
            assert login.json()["outcome"] == "ok"
            csrf = login.json()["csrf_token"]

            key = ec.generate_private_key(ec.SECP256R1())
            register = await post("/auth/mfa/webauthn/register", {}, csrf)
            assert register.status_code == 200, register.text
            completed = await post(
                "/auth/mfa/webauthn/register-complete",
                {
                    "name": "browser passkey",
                    "credential": _credential(key, register.json()["challenge"]),
                },
                csrf,
            )
            assert completed.status_code == 201, completed.text

            client.cookies.clear()
            login = await post("/auth/login", credentials)
            assert login.json()["outcome"] == "mfa_required"
            csrf = login.json()["csrf_token"]
            assert (await client.get("/api/echo")).status_code == 401

            options = await post("/auth/mfa/webauthn/authenticate", {}, csrf)
            assert options.status_code == 200, options.text
            assertion = _assertion(key, options.json()["challenge"])
            issuer = served.runtime.broker.issuer
            previous = issuer.settings
            issuer._settings = replace(previous, issuer="https://other.example")
            changed = await post(
                "/auth/mfa/webauthn/authenticate-complete",
                {"credential": assertion},
                csrf,
            )
            assert changed.status_code == 409
            assert changed.json() == {"error": "webauthn_origin_changed"}
            issuer._settings = previous
            replay = await post(
                "/auth/mfa/webauthn/authenticate-complete",
                {"credential": assertion},
                csrf,
            )
            assert replay.status_code == 400
            assert replay.json() == {"error": "webauthn_challenge_expired"}
            assert (await client.get("/api/echo")).status_code == 401

            fresh = await post("/auth/mfa/webauthn/authenticate", {}, csrf)
            assert fresh.status_code == 200, fresh.text
            verified = await post(
                "/auth/mfa/webauthn/authenticate-complete",
                {"credential": _assertion(key, fresh.json()["challenge"])},
                csrf,
            )
            assert verified.status_code == 200, verified.text
            admitted = await client.get("/api/echo")
            assert admitted.status_code == 200, admitted.text
            assert scope in admitted.json()["scope"].split()

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
