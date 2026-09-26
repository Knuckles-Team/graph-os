"""Gate verified browser authority is the only path to CONSOLE invoke."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from graph_os.api.http.auth import AmbientHTTPAuthenticator, HTTPAuthenticationError
from graph_os.api.http.routes import make_endpoint
from graph_os.api.registry import Surface
from graph_os.identity.admission import Admission
from graph_os.identity.browser import SESSION_COOKIE, csrf_token_for
from graph_os.identity.gate import IdentityGate


class Session:
    def __init__(self) -> None:
        self.actor = SimpleNamespace(
            actor_id="usr:alice",
            actor_type="human",
            authenticated=True,
            ensure_credential_current=lambda: None,
        )
        self.tenant = "local"
        self.scopes = {"identity:admin"}
        self.policy_version = "1"

    def ensure_authority_current(self) -> None:
        pass

    def engine_verified_context(self) -> dict[str, object]:
        return {"sub": "usr:alice", "tenant_id": "local"}


@pytest.mark.asyncio
async def test_gate_to_http_console_requires_verified_session_mfa(monkeypatch) -> None:
    from agent_utilities.knowledge_graph.core import session as session_module

    monkeypatch.setattr(session_module, "resolve_session", lambda: Session())
    now = int(time.time() * 1000)
    token = "a" * 43
    headers = [
        (b"cookie", f"{SESSION_COOKIE}={token}".encode()),
        (b"host", b"console.example.test"),
        (b"origin", b"https://console.example.test"),
        (b"x-csrf-token", csrf_token_for(token).encode()),
    ]
    auth = AmbientHTTPAuthenticator(console_origin="https://console.example.test")
    observed = []

    async def app(scope, receive, send):
        request = Request(scope, receive)
        caller = await auth.authenticate(request)
        observed.append((caller, auth.is_console_request(request, caller)))

    issuer = SimpleNamespace(
        verify=lambda _: {"sub": "usr:alice", "tenant_id": "local"}
    )
    gate = IdentityGate(
        app,
        admission=SimpleNamespace(broker=SimpleNamespace(issuer=issuer)),
        routes=[],
    )
    scope = {
        "type": "http",
        "path": "/api/v1/ops/identity.users.disable",
        "method": "POST",
        "scheme": "https",
        "headers": headers,
        "state": {"graphos_console_mfa_at_ms": now},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        pass

    await gate._forward(
        Admission(
            token="verified.jwt",
            session_principal_id="usr:alice",
            session_mfa_at_ms=now,
        ),
        scope,
        receive,
        send,
    )
    caller, is_console = observed.pop()
    assert caller.mfa_at_ms == now
    assert is_console is True

    # An incoming marker is erased by the gate when admission has no session.
    with pytest.raises(HTTPAuthenticationError):
        await gate._forward(Admission(), scope, receive, send)

    await gate._forward(
        Admission(token="verified.jwt", session_principal_id="usr:alice"),
        scope,
        receive,
        send,
    )
    caller, is_console = observed.pop()
    assert caller.mfa_at_ms is None
    assert is_console is False


@pytest.mark.asyncio
async def test_http_endpoint_invokes_with_classified_surface(monkeypatch) -> None:
    from graph_os.api import invoke as invoke_module

    surfaces: list[Surface] = []

    async def invoke(_op_id, _params, _caller, surface, **_kwargs):
        surfaces.append(surface)
        return SimpleNamespace(value={"ok": True})

    monkeypatch.setattr(invoke_module, "invoke", invoke)
    caller = SimpleNamespace(request_id="req")

    async def authenticate(_request):
        return caller

    endpoint = make_endpoint(
        SimpleNamespace(id="identity.users.admin_reset"),
        services=SimpleNamespace(registry=SimpleNamespace(digest="digest")),
        authenticate=authenticate,
        response=lambda result, _op, _request_id: result,
        generic=True,
        is_console_request=lambda request, _: (
            request.scope.get("state", {}).get("graphos_session_admitted") is True
        ),
    )

    def request(admitted: bool) -> Request:
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/ops/identity.users.admin_reset",
                "headers": [(b"content-type", b"application/json")],
                "query_string": b"",
                "state": {"graphos_session_admitted": admitted},
            },
            receive,
        )

    await endpoint(request(False))
    await endpoint(request(True))
    assert surfaces == [Surface.HTTP, Surface.CONSOLE]
