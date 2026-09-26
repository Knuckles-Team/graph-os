"""Ceremony challenges stay bound to the original browser context."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

import graph_os.identity.web_webauthn as web_webauthn
from graph_os.identity.broker import IdentityBroker
from graph_os.identity.engine import IdentityReply
from graph_os.identity.web_common import guarded
from graph_os.identity.web_webauthn import RouteError, WebauthnCeremonies

ORIGIN = "https://localhost:8443"


def test_enrollment_required_preserves_pending_session_cookie_material() -> None:
    class Engine:
        async def broker(self, _call: object) -> IdentityReply:
            return IdentityReply("authenticate", {"outcome": "mfa_enrollment_required"})

    async def exercise() -> None:
        broker = IdentityBroker(Engine(), SimpleNamespace())
        opened = await broker.sign_in("admin", "password")
        assert opened.sign_in.outcome == "mfa_enrollment_required"
        assert opened.session_token

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("required", "enrolled", "allowed"),
    [(True, False, True), (True, True, False), (False, False, False)],
)
def test_webauthn_enrollment_pending_policy(
    monkeypatch: pytest.MonkeyPatch, required: bool, enrolled: bool, allowed: bool
) -> None:
    ceremonies = _ceremonies()
    caller = SimpleNamespace(
        resolution=SimpleNamespace(
            session_mfa_pending=True, mfa_required=required, mfa_enrolled=enrolled
        )
    )

    async def fake_caller(*_args: object, **kwargs: object) -> object:
        assert kwargs == {"pending_ok": True}
        return caller

    monkeypatch.setattr(web_webauthn, "caller_of", fake_caller)

    async def exercise() -> None:
        if allowed:
            assert await ceremonies._registration_caller(object()) is caller
        else:
            with pytest.raises(RouteError) as error:
                await ceremonies._registration_caller(object())
            assert (error.value.status, error.value.reason) == (
                401,
                "second_factor_required",
            )

    asyncio.run(exercise())


def _ceremonies() -> WebauthnCeremonies:
    return WebauthnCeremonies(SimpleNamespace(broker=object()))


def _refusal(operation, status: int, reason: str) -> None:
    with pytest.raises(RouteError) as error:
        operation()
    assert (error.value.status, error.value.reason) == (status, reason)


def test_challenge_is_single_use_and_bound_to_session_purpose_principal() -> None:
    ceremonies = _ceremonies()
    value = ceremonies._issue("session-a", "authenticate", "alice", ORIGIN)
    _refusal(
        lambda: ceremonies._consume("session-b", "authenticate", "alice", ORIGIN),
        400,
        "webauthn_challenge_expired",
    )
    _refusal(
        lambda: ceremonies._consume("session-a", "register", "alice", ORIGIN),
        400,
        "webauthn_challenge_expired",
    )
    assert ceremonies._consume("session-a", "authenticate", "alice", ORIGIN) == value
    _refusal(
        lambda: ceremonies._consume("session-a", "authenticate", "alice", ORIGIN),
        400,
        "webauthn_challenge_expired",
    )

    ceremonies._issue("session-a", "authenticate", "alice", ORIGIN)
    _refusal(
        lambda: ceremonies._consume("session-a", "authenticate", "bob", ORIGIN),
        403,
        "webauthn_principal_changed",
    )
    _refusal(
        lambda: ceremonies._consume("session-a", "authenticate", "alice", ORIGIN),
        400,
        "webauthn_challenge_expired",
    )


def test_origin_drift_consumes_challenge_before_assertion_verification() -> None:
    ceremonies = _ceremonies()
    ceremonies._issue("session", "register", "alice", ORIGIN)
    _refusal(
        lambda: ceremonies._consume(
            "session", "register", "alice", "https://other.example"
        ),
        409,
        "webauthn_origin_changed",
    )
    _refusal(
        lambda: ceremonies._consume("session", "register", "alice", ORIGIN),
        400,
        "webauthn_challenge_expired",
    )


def test_issuing_new_challenge_replaces_earlier_one() -> None:
    ceremonies = _ceremonies()
    first = ceremonies._issue("session", "authenticate", "alice", ORIGIN)
    second = ceremonies._issue("session", "authenticate", "alice", ORIGIN)
    assert first != second
    assert ceremonies._consume("session", "authenticate", "alice", ORIGIN) == second


@pytest.mark.parametrize(
    ("purpose", "body"),
    [
        ("register", {"name": "passkey", "credential": {}}),
        ("authenticate", {"credential": {"id": "credential"}}),
    ],
)
def test_completion_route_consumes_challenge_after_origin_change(
    monkeypatch: pytest.MonkeyPatch, purpose: str, body: dict
) -> None:
    broker = SimpleNamespace(
        issuer=SimpleNamespace(settings=SimpleNamespace(issuer=ORIGIN))
    )
    ceremonies = WebauthnCeremonies(SimpleNamespace(broker=broker))
    ceremonies._issue("session", purpose, "alice", ORIGIN)
    caller = SimpleNamespace(
        session_token="session",
        resolution=SimpleNamespace(
            principal_id="alice", session_mfa_pending=purpose == "authenticate"
        ),
    )

    async def caller_of(*_args: object, **_kwargs: object) -> object:
        return caller

    monkeypatch.setattr(web_webauthn, "caller_of", caller_of)
    handler = (
        ceremonies.register_complete
        if purpose == "register"
        else ceremonies.authenticate_complete
    )
    app = Starlette(routes=[Route("/complete", guarded(handler), methods=["POST"])])

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            broker.issuer.settings.issuer = "https://other.example"
            changed = await client.post("/complete", json=body)
            assert changed.status_code == 409
            assert changed.json() == {"error": "webauthn_origin_changed"}
            broker.issuer.settings.issuer = ORIGIN
            replay = await client.post("/complete", json=body)
            assert replay.status_code == 400
            assert replay.json() == {"error": "webauthn_challenge_expired"}

    asyncio.run(exercise())
