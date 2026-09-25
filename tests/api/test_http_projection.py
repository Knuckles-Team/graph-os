"""Focused HTTP adapter guards; shared invoke/registry integration runs at cutover."""

from __future__ import annotations

import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from graph_os.api.http.app import _subapp_path, create_api_application
from graph_os.api.http.auth import AmbientHTTPAuthenticator, HTTPAuthenticationError
from graph_os.api.http.openapi import document
from graph_os.api.http.protocol_routes import PROTOCOL_ROUTES
from graph_os.api.http.routes import _params, make_endpoint
from graph_os.api.registry import Surface


def request(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]] = (),
    body: bytes = b"",
    state: dict | None = None,
) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
            "state": state or {},
        },
        receive,
    )


@pytest.mark.asyncio
async def test_missing_authority_fails_closed() -> None:
    with pytest.raises(HTTPAuthenticationError):
        await AmbientHTTPAuthenticator().authenticate(
            request("GET", "/api/v1/registry")
        )


@pytest.mark.asyncio
async def test_cookie_requires_gate_admission_csrf_and_signed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_utilities.api import session as session_module

    from graph_os.api import invoke as invoke_module

    session = SimpleNamespace(
        tenant="tenant-a", actor=SimpleNamespace(actor_id="alice")
    )
    monkeypatch.setattr(session_module, "resolve_session", lambda: session)

    def from_session(value, *, request_id, mfa_at_ms=None):
        assert value is session
        return SimpleNamespace(
            principal="alice",
            tenant="tenant-a",
            request_id=request_id,
            mfa_at_ms=mfa_at_ms,
            credential_kind="session",
            principal_kind="human",
            delegated=False,
        )

    monkeypatch.setattr(invoke_module.VerifiedCaller, "from_session", from_session)
    identity_module = ModuleType("graph_os.identity")
    identity_module.__path__ = []  # type: ignore[attr-defined]
    browser = ModuleType("graph_os.identity.browser")
    browser.session_from_scope = (  # type: ignore[attr-defined]
        lambda scope: (
            "opaque"
            if [v for k, v in scope["headers"] if k == b"cookie"]
            == [b"__Host-graphos_session=opaque"]
            else None
        )
    )
    browser.csrf_refusal = (  # type: ignore[attr-defined]
        lambda scope, token: (
            None
            if token == "opaque"
            and (b"origin", b"https://console.example.test") in scope["headers"]
            and (b"x-csrf-token", b"secret") in scope["headers"]
            else "csrf_refused"
        )
    )
    monkeypatch.setitem(sys.modules, "graph_os.identity", identity_module)
    monkeypatch.setitem(sys.modules, "graph_os.identity.browser", browser)
    auth = AmbientHTTPAuthenticator(console_origin="https://console.example.test")
    cookie = (b"cookie", b"__Host-graphos_session=opaque")
    bearer = (b"authorization", b"Bearer gate-minted")
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(
            request("POST", "/api/v1/ops/example", [cookie, bearer])
        )
    common = [
        cookie,
        bearer,
        (b"origin", b"https://console.example.test"),
        (b"x-csrf-token", b"secret"),
    ]
    with pytest.raises(HTTPAuthenticationError):
        await auth.authenticate(
            request(
                "POST",
                "/api/v1/ops/example",
                common,
                state={
                    "graphos_session_admitted": True,
                    "user_claims": {"sub": "bob", "tenant_id": "tenant-a"},
                },
            )
        )
    now = int(time.time() * 1000)
    good = request(
        "POST",
        "/api/v1/ops/example",
        common,
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "alice", "tenant_id": "tenant-a"},
            "graphos_console_mfa_at_ms": now,
        },
    )
    caller = await auth.authenticate(good)
    assert caller.principal == "alice"
    assert caller.mfa_at_ms == now
    assert auth.is_console_request(good, caller)
    plain_bearer = request("GET", "/api/v1/registry", [bearer])
    bearer_caller = await auth.authenticate(plain_bearer)
    assert bearer_caller.principal == "alice"
    assert not auth.is_console_request(plain_bearer, bearer_caller)
    stale = request(
        "POST",
        "/api/v1/ops/example",
        common,
        state={
            "graphos_session_admitted": True,
            "user_claims": {"sub": "alice", "tenant_id": "tenant-a"},
            "graphos_console_mfa_at_ms": now - 16 * 60 * 1000,
        },
    )
    assert (await auth.authenticate(stale)).mfa_at_ms is None


@pytest.mark.asyncio
async def test_http_params_refuse_duplicate_and_non_object_body() -> None:
    headers = [(b"content-type", b"application/json")]
    assert await _params(
        request("POST", "/ops/example", headers, b'{"x":1}'), generic=True
    ) == {"x": 1}
    with pytest.raises(ValueError):
        await _params(request("POST", "/ops/example", headers, b"[]"), generic=True)


def test_openapi_contains_only_supplied_ops() -> None:
    model = SimpleNamespace(model_json_schema=lambda: {"type": "object"})
    op = SimpleNamespace(
        id="query.uql", summary="Run UQL", http=None, params=model, result=model
    )
    spec = document([op], digest="abc")
    assert list(spec["paths"]) == ["/api/v1/ops/query.uql"]
    assert spec["x-registry-digest"] == "abc"


def test_protocol_inventory_records_native_surfaces() -> None:
    paths = {item.path for item in PROTOCOL_ROUTES}
    assert {"/a2a", "/mcp", "/health", "/fleet/events", "/scim/v2/{path:path}"} <= paths


def test_v1_subapp_mount_has_one_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("graph_os.api.registry")
    module.Surface = SimpleNamespace(HTTP="http")  # type: ignore[attr-defined]
    module.canonical_op = lambda op: {"id": op.id}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph_os.api.registry", module)
    op = SimpleNamespace(
        id="identity.users.list",
        surfaces={"http"},
        http=SimpleNamespace(method="GET", path="/api/v1/identity/users"),
    )

    class Registry:
        digest = "abc"

        def __iter__(self):
            return iter((op,))

        def find(self, caller, *, surface, policy):
            return (op,) if surface == "http" and policy(op, caller) else ()

    class Auth:
        async def authenticate(self, request):
            return SimpleNamespace(authenticated=True)

        def is_console_request(self, request, caller):
            return False

    async def visibility(op, caller):
        return True

    child = create_api_application(
        services=SimpleNamespace(registry=Registry()),
        visibility=visibility,
        authenticator=Auth(),  # type: ignore[arg-type]
    )
    assert "/identity/users" in {route.path for route in child.routes}
    parent = FastAPI()
    parent.mount("/api/v1", child)
    with TestClient(parent) as client:
        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        assert response.json()["ops"] == [{"id": op.id}]
        assert client.get("/api/v1/api/v1/registry").status_code == 404


def test_resource_path_rejects_other_api_versions() -> None:
    assert _subapp_path("/api/v1/identity/users") == "/identity/users"
    assert _subapp_path("/identity/users") == "/identity/users"
    with pytest.raises(ValueError):
        _subapp_path("/api/v2/identity/users")


@pytest.mark.asyncio
async def test_console_requires_verified_cookie_origin_human_and_fresh_mfa() -> None:
    now = int(time.time() * 1000)
    caller = SimpleNamespace(
        authenticated=True,
        credential_kind="session",
        principal_kind="human",
        delegated=False,
        mfa_at_ms=now,
    )

    auth = AmbientHTTPAuthenticator(console_origin="https://console.example.test")
    headers = [
        (b"cookie", b"__Host-graphos_session=opaque"),
        (b"origin", b"https://console.example.test"),
        (b"x-csrf-token", b"csrf-secret"),
    ]
    attended = request(
        "POST",
        "/api/v1/ops/identity.users.disable",
        headers,
        state={"graphos_session_admitted": True},
    )
    assert auth.is_console_request(attended, caller)
    assert not AmbientHTTPAuthenticator().is_console_request(attended, caller)
    for changed in (
        {"credential_kind": "bearer"},
        {"principal_kind": "service"},
        {"delegated": True},
        {"mfa_at_ms": now - 901_000},
        {"mfa_at_ms": now + 60_000},
        {"mfa_at_ms": None},
    ):
        facts = vars(caller) | changed
        assert not auth.is_console_request(attended, SimpleNamespace(**facts))
    foreign = request(
        "POST",
        "/api/v1/ops/identity.users.disable",
        [headers[0], (b"origin", b"https://other.example.test"), headers[2]],
        state={"graphos_session_admitted": True},
    )
    assert not auth.is_console_request(foreign, caller)
    assert not auth.is_console_request(
        request("POST", "/api/v1/ops/identity.users.disable", headers), caller
    )


@pytest.mark.asyncio
async def test_console_projection_passes_only_verified_surface(monkeypatch) -> None:
    from graph_os.api import invoke as invoke_module

    seen: list[tuple[Surface, str | None]] = []

    async def fake_invoke(op_id, params, caller, surface, **kwargs):
        seen.append((surface, kwargs["plan_ref"]))
        return SimpleNamespace(value={"ok": True})

    monkeypatch.setattr(invoke_module, "invoke", fake_invoke)
    caller = SimpleNamespace(request_id="req")
    endpoint = make_endpoint(
        SimpleNamespace(id="identity.users.disable"),
        services=SimpleNamespace(registry=SimpleNamespace(digest="digest")),
        authenticate=lambda _: _return_caller(caller),
        response=lambda outcome, op, request_id: SimpleNamespace(
            outcome=outcome, op=op, request_id=request_id
        ),
        generic=True,
        is_console_request=lambda req, _: (
            req.headers.get("origin") == "https://console.example.test"
        ),
    )
    common = [
        (b"content-type", b"application/json"),
        (b"graphos-plan-ref", b"graphos_plan:reference"),
    ]
    await endpoint(request("POST", "/api/v1/ops/identity.users.disable", common, b"{}"))
    await endpoint(
        request(
            "POST",
            "/api/v1/ops/identity.users.disable",
            [*common, (b"origin", b"https://console.example.test")],
            b"{}",
        )
    )
    assert seen == [
        (Surface.HTTP, "graphos_plan:reference"),
        (Surface.CONSOLE, "graphos_plan:reference"),
    ]


async def _return_caller(caller):
    return caller
