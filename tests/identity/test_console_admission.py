"""Only a live browser session may carry console provenance downstream."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from graph_os.identity.admission import Admission
from graph_os.identity.gate import IdentityGate

from .gate_harness import SETUP_CODE, serve
from .store_double import StoreDouble


def _local():
    served = serve(StoreDouble(), profile="single-node-prod")
    assert (
        served.post(
            "/auth/setup",
            {
                "setup_code": SETUP_CODE,
                "username": "root",
                "password": "correct horse battery staple",
            },
        ).status_code
        == 200
    )
    return served


def test_live_session_has_admitted_marker_but_no_inferred_mfa() -> None:
    served = _local()
    body = served.get("/api/echo").json()
    assert body["session_admitted"] is True
    assert body["session_token_present"] is True
    assert body["console_mfa_at_ms"] is None
    assert body["sub"] is not None


def test_only_broker_session_mfa_time_is_forwarded() -> None:
    served = _local()
    original = served.runtime.broker.resolve_session

    async def resolved_with_mfa(session_token: str):
        resolution = await original(session_token)
        assert resolution is not None
        return replace(resolution, session_mfa_at_ms=1_797_027_200_000)

    served.runtime.broker.resolve_session = resolved_with_mfa
    body = served.get("/api/echo").json()
    assert body["session_admitted"] is True
    assert body["console_mfa_at_ms"] == 1_797_027_200_000


def test_cookie_and_bearer_together_cannot_gain_session_marker() -> None:
    served = _local()
    response = served.get("/api/echo", authorization="Bearer upstream.jwt.value")
    assert response.status_code == 401
    assert response.json() == {"error": "ambiguous_credentials"}


def test_csrf_refusal_precedes_console_marker() -> None:
    served = _local()
    served.csrf = "forged"
    response = served.post("/api/echo")
    assert response.status_code == 403
    assert response.json() == {"error": "csrf_token_mismatch"}


def test_revoked_session_cannot_gain_session_marker() -> None:
    served = _local()
    for session in served.store.sessions.values():
        session.revoked = True
    body = served.get("/api/echo").json()
    assert body["session_admitted"] is None
    assert body["session_token_present"] is False
    assert body["console_mfa_at_ms"] is None


def test_incoming_asgi_state_cannot_assert_console_provenance() -> None:
    served = _local()
    captured = {}

    async def app(scope, receive, send):
        captured.update(scope["state"])

    gate = IdentityGate(app, admission=served.runtime.admission, routes=[])
    scope = {
        "type": "http",
        "path": "/api/echo",
        "method": "GET",
        "headers": [],
        "state": {
            "graphos_session_admitted": True,
            "graphos_identity_session_token": "caller-planted-token",
            "graphos_console_mfa_at_ms": 1_797_027_200_000,
        },
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    asyncio.run(gate._forward(Admission(), scope, receive, send))
    assert "graphos_session_admitted" not in captured
    assert "graphos_identity_session_token" not in captured
    assert "graphos_console_mfa_at_ms" not in captured
